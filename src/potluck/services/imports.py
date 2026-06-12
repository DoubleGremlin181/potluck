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
from potluck.ingest.plugins import ParseContext, detect_sources, discover
from potluck.ingest.readers import open_archive
from potluck.models.imports import ImportRun
from potluck.services.context import AppContext
from potluck.storage import imports as _storage_imports

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

        started = time.perf_counter()
        plugins = detect_sources(archive)
        detect_s = time.perf_counter() - started
        if not plugins:
            registered = ", ".join(sorted(discover())) or "(none)"
            raise UnknownSourceError(
                f"no source plugin recognises '{path}'; registered sources: {registered}"
            )

        started = time.perf_counter()
        fhash: str | None = _file_hash(path) if path.is_file() else None
        hash_s = time.perf_counter() - started
        _logger.info(
            "import %s: detected %s (detect %.2fs, file-hash %.2fs)",
            path,
            ", ".join(p.name for p in plugins),
            detect_s,
            hash_s,
        )

        parse_ctx = ParseContext(
            attachments_dir=(
                ctx.settings.attachments_dir if ctx.settings.extract_attachments else None
            )
        )

        import_ids: list[int] = []
        for plugin in plugins:
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
