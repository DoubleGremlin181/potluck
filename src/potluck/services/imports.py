"""Imports service: open-archive → detect-sources → one import run per source.

This is the seam the CLI/API/MCP layers use; they never reach into ingest
directly. Phase timings (detect / file-hash / per-source parse+write) are
logged at INFO — the #196 measurement surface.
"""

import contextlib
import logging
import tarfile
import time
import uuid
import zipfile
from functools import partial
from pathlib import Path
from typing import BinaryIO, Final

from potluck.core.errors import (
    ImportInProgressError,
    UnknownSourceError,
    UnsupportedArchiveError,
    UploadTooLargeError,
)
from potluck.ingest.engine import run_import
from potluck.ingest.hashing import file_hash as _file_hash
from potluck.ingest.plugins import ParseContext, detect_sources, discover, registry_fingerprint
from potluck.ingest.readers import MultiPartArchive, open_archive
from potluck.models.imports import (
    ImportListResponse,
    ImportRun,
    ImportTask,
    SourceInfo,
)
from potluck.services.context import AppContext
from potluck.storage import imports as _storage_imports
from potluck.storage import scans as _storage_scans

_logger = logging.getLogger(__name__)

_UPLOAD_CHUNK_BYTES: Final = 1024 * 1024


def recover_interrupted_imports(ctx: AppContext) -> int:
    """Mark stale 'running' import rows failed('interrupted'); return the count.

    A run left 'running' by a crash/kill can never resume — but only a
    process taking WRITE OWNERSHIP of the imports ledger may decide that a
    'running' row is stale. Call sites are exactly those ownership moments:
    API serve startup (before the first request can be served) and the top
    of :func:`import_path` (every import run — background manager and CLI
    alike). Read-only contexts (status/search/show) must never call this, or
    they would mark another process's live import as interrupted mid-run.
    """
    interrupted = int(ctx.db.write(_storage_imports.fail_stale_running_imports))
    if interrupted:
        _logger.warning("marked %d interrupted import run(s) as failed", interrupted)
    return interrupted


def import_path(ctx: AppContext, path: Path) -> list[ImportRun]:
    """Open the archive at *path* and run one import per detected source.

    Takes write ownership of the imports ledger: stale 'running' rows left by
    a crashed process are swept to failed('interrupted') before this run
    begins (see :func:`recover_interrupted_imports`).

    Returns the completed ledger rows in detection order (sorted by source
    name). A failure in source N marks ITS ledger row failed and re-raises;
    earlier sources' runs stay committed — re-running the import completes
    the rest (idempotent thanks to content-hash dedup).

    Raises:
        UnsupportedArchiveError: if *path* does not exist, or is a corrupt/
            truncated zip or tar (translated from the stdlib errors so
            interface layers only handle PotluckError). An existing plain
            file of any other format opens as a SingleFileArchive instead.
        UnknownSourceError: if no registered plugin matches the archive contents.

    File hash semantics: single-file archives → sha256 of the passed file;
    multi-part archives → sha256 of the PASSED PART only (not the full set);
    directories → None.
    """
    recover_interrupted_imports(ctx)
    # The try spans detection AND parsing: archives are read lazily, so a
    # truncated zip can surface BadZipFile mid-import, not just at open.
    try:
        archive = open_archive(path)

        # Hash FIRST (raw read, no decompression — ~3 s for a 3.8 GB file):
        # it keys both the detection cache and the per-source ledger
        # short-circuit. Detection itself costs a full decompression pass for
        # tgz (~73 s measured on a real Takeout), and is a pure function of
        # (archive bytes, registered globs) — so a cached outcome for this
        # exact (file_hash, registry fingerprint) skips the scan entirely.
        started = time.perf_counter()
        fhash: str | None = _file_hash(path) if path.is_file() else None
        hash_s = time.perf_counter() - started

        registry = discover()
        registry_fp = registry_fingerprint(registry)
        # fhash covers only the PASSED part, but detection (and the ledger)
        # see the whole logical set — both caches are disabled for multi-part
        # sets, or a part-1 cache entry could mask sources living in parts
        # that appeared after it was recorded.
        single_part = not isinstance(archive, MultiPartArchive)
        cached_names: list[str] | None = None
        if fhash is not None and single_part:
            with ctx.db.read() as conn:
                cached_names = _storage_scans.get_scan(conn, fhash, registry_fp)

        started = time.perf_counter()
        if cached_names is not None:
            plugins = [registry[name] for name in cached_names]
            detect_note = "cached"
        else:
            plugins = detect_sources(archive)
            detect_note = f"{time.perf_counter() - started:.2f}s"
            if fhash is not None and single_part:
                matched_names = [p.name for p in plugins]
                ctx.db.write(
                    lambda conn: _storage_scans.record_scan(conn, fhash, registry_fp, matched_names)
                )
        if not plugins:
            registered = ", ".join(sorted(registry)) or "(none)"
            raise UnknownSourceError(
                f"no source plugin recognises '{path}'; registered sources: {registered}"
            )

        _logger.info(
            "import %s: detected %s (detect %s, file-hash %.2fs)",
            path,
            ", ".join(p.name for p in plugins),
            detect_note,
            hash_s,
        )

        parse_ctx = ParseContext(
            attachments_dir=(
                ctx.settings.attachments_dir if ctx.settings.extract_attachments else None
            ),
            workers=ctx.settings.ingest_workers,
        )

        # Ledger short-circuit (#126): same file bytes + same parser version
        # for a source means re-parsing cannot change anything — return the
        # prior completed run instead of re-reading a multi-GB archive.
        # Disabled for multi-part sets: fhash covers only the passed part.
        can_short_circuit = fhash is not None and single_part

        import_ids: list[int] = []
        for plugin in plugins:
            if can_short_circuit:
                assert fhash is not None
                with ctx.db.read() as conn:
                    prior = _storage_imports.find_completed_import(
                        conn,
                        source_name=plugin.name,
                        file_hash=fhash,
                        parser_version=plugin.parser_version,
                        extract_attachments=ctx.settings.extract_attachments,
                    )
                if prior is not None:
                    _logger.info(
                        "import %s: source %s unchanged — already imported as #%d",
                        path,
                        plugin.name,
                        prior.id,
                    )
                    import_ids.append(prior.id)
                    continue

            started = time.perf_counter()
            import_ids.append(
                run_import(
                    ctx.db,
                    source_name=plugin.name,
                    parser_version=plugin.parser_version,
                    drafts=plugin.parse(archive, parse_ctx),
                    path=str(path),
                    file_hash=fhash,
                    extract_attachments=ctx.settings.extract_attachments,
                )
            )
            _logger.info(
                "import %s: source %s done in %.2fs",
                path,
                plugin.name,
                time.perf_counter() - started,
            )
    except (zipfile.BadZipFile, tarfile.ReadError) as exc:
        raise UnsupportedArchiveError(f"corrupt or unreadable archive: {path}: {exc}") from exc

    with ctx.db.read() as conn:
        return [_storage_imports.get_import(conn, import_id) for import_id in import_ids]


def list_imports(ctx: AppContext, limit: int = 50, offset: int = 0) -> ImportListResponse:
    """One page of import history, newest first, with the unpaginated total."""
    with ctx.db.read() as conn:
        return ImportListResponse(
            runs=_storage_imports.list_imports(conn, limit, offset),
            total=_storage_imports.count_imports(conn),
        )


def get_import(ctx: AppContext, import_id: int) -> ImportRun:
    """One import row including its progress fields (the UI's poll target).

    Raises:
        ImportNotFoundError: if no import run has this id.
    """
    with ctx.db.read() as conn:
        return _storage_imports.get_import(conn, import_id)


def start_import(ctx: AppContext, path: Path) -> ImportTask:
    """Start a background import of *path*; return the initial task snapshot.

    The import runs on the context's single import worker thread and drives
    the exact same :func:`import_path` the CLI uses — all writes still funnel
    through the database's single writer thread. Progress is polled from the
    imports row (updated once per committed batch); the returned task is the
    handle for the detection phase, before any row exists.

    The server-path variant is localhost-only by design (v1 threat model):
    the API binds 127.0.0.1 and its only client is the machine's owner, so
    reading an arbitrary local path is the feature, not a traversal risk.

    Raises:
        UnsupportedArchiveError: if *path* does not exist (fail fast on typos;
            all other archive errors surface on the task, then the ledger).
        ImportInProgressError: if an import is already running (HTTP 409).
    """
    if not path.exists():
        raise UnsupportedArchiveError(f"no such file or directory: {path}")
    return ctx.import_manager.start(partial(import_path, ctx, path), path=str(path))


def import_status(ctx: AppContext) -> ImportTask | None:
    """Snapshot of the current/last background import; None before any start."""
    return ctx.import_manager.status()


def store_upload(ctx: AppContext, filename: str, stream: BinaryIO) -> Path:
    """Persist an uploaded archive into the managed uploads dir; return its path.

    Path-traversal sanity: only the basename of the client-supplied filename
    is used, inside a fresh unique subdirectory of ``settings.uploads_dir`` —
    a name like ``../../evil.zip`` lands as ``uploads/<token>/evil.zip``.

    Size cap: the copy stops at ``settings.max_upload_bytes`` (default
    10 GiB — a real Takeout part is at most ~50 GB split into parts, 10 GiB
    covers the common 2/4/10 GB splits) and the partial file is removed.
    This guards the managed store; capping the request body itself before it
    reaches the handler is a front-server concern.

    Raises:
        UnsupportedArchiveError: if the filename has no usable basename.
        UploadTooLargeError: if the payload exceeds ``settings.max_upload_bytes``.
    """
    name = Path(filename.replace("\\", "/")).name
    if name in ("", ".", ".."):
        raise UnsupportedArchiveError(f"invalid upload filename: {filename!r}")
    limit = ctx.settings.max_upload_bytes
    dest_dir = ctx.settings.uploads_dir / uuid.uuid4().hex
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    copied = 0
    try:
        with dest.open("wb") as out:
            while chunk := stream.read(_UPLOAD_CHUNK_BYTES):
                copied += len(chunk)
                if copied > limit:
                    raise UploadTooLargeError(
                        f"upload '{name}' exceeds the configured limit of {limit} bytes "
                        "(settings: max_upload_bytes)"
                    )
                out.write(chunk)
    except BaseException:
        # Never leave a partial (or oversized) file in the managed store.
        dest.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            dest_dir.rmdir()
        raise
    return dest


def start_upload_import(ctx: AppContext, filename: str, stream: BinaryIO) -> ImportTask:
    """Store an uploaded archive, then start the background import over it.

    The busy check runs BEFORE the upload is written so a conflicting request
    fails without copying gigabytes first; the manager re-checks atomically
    at start (a lost race can still 409 after storing — the orphaned file
    stays in the uploads dir, harmless).

    Raises:
        ImportInProgressError: if an import is already running (HTTP 409).
        UnsupportedArchiveError: if the filename has no usable basename.
    """
    task = ctx.import_manager.status()
    if task is not None and task.status == "running":
        raise ImportInProgressError(
            f"an import of '{task.path}' is already running; only one import runs at a time"
        )
    return start_import(ctx, store_upload(ctx, filename, stream))


def list_sources(ctx: AppContext) -> list[SourceInfo]:
    """Registered source plugins (name + item kinds), sorted by name.

    A pure registry read — *ctx* is unused today but keeps the uniform
    ``(ctx, req) -> resp`` service shape.
    """
    del ctx
    return [
        SourceInfo(name=plugin.name, kinds=list(plugin.kinds))
        for _, plugin in sorted(discover().items())
    ]
