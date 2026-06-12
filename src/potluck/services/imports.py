"""Imports service: open-archive → detect-sources → one import run per source.

This is the seam the CLI/API/MCP layers use; they never reach into ingest
directly. Phase timings (detect / file-hash / per-source parse+write) are
logged at INFO — the #196 measurement surface.
"""

import logging
import tarfile
import time
import zipfile
from pathlib import Path

from potluck.core.errors import UnknownSourceError, UnsupportedArchiveError
from potluck.ingest.engine import run_import
from potluck.ingest.hashing import file_hash as _file_hash
from potluck.ingest.plugins import ParseContext, detect_sources, discover, registry_fingerprint
from potluck.ingest.readers import MultiPartArchive, open_archive
from potluck.models.imports import ImportRun
from potluck.services.context import AppContext
from potluck.storage import imports as _storage_imports
from potluck.storage import scans as _storage_scans

_logger = logging.getLogger(__name__)


def import_path(ctx: AppContext, path: Path) -> list[ImportRun]:
    """Open the archive at *path* and run one import per detected source.

    Returns the completed ledger rows in detection order (sorted by source
    name). A failure in source N marks ITS ledger row failed and re-raises;
    earlier sources' runs stay committed — re-running the import completes
    the rest (idempotent thanks to content-hash dedup).

    Raises:
        UnsupportedArchiveError: if *path* is not a recognised archive format,
            or is a corrupt/truncated zip or tar (translated from the stdlib
            errors so interface layers only handle PotluckError).
        UnknownSourceError: if no registered plugin matches the archive contents.

    File hash semantics: single-file archives → sha256 of the passed file;
    multi-part archives → sha256 of the PASSED PART only (not the full set);
    directories → None.
    """
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
        cached_names: list[str] | None = None
        if fhash is not None:
            with ctx.db.read() as conn:
                cached_names = _storage_scans.get_scan(conn, fhash, registry_fp)

        started = time.perf_counter()
        if cached_names is not None:
            plugins = [registry[name] for name in cached_names]
            detect_note = "cached"
        else:
            plugins = detect_sources(archive)
            detect_note = f"{time.perf_counter() - started:.2f}s"
            if fhash is not None:
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
        can_short_circuit = fhash is not None and not isinstance(archive, MultiPartArchive)

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


def list_imports(ctx: AppContext, limit: int = 50) -> list[ImportRun]:
    """Return import runs ordered newest-first, capped at *limit*."""
    with ctx.db.read() as conn:
        return _storage_imports.list_imports(conn, limit)
