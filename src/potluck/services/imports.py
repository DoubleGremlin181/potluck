"""Imports service: open-archive → detect-source → run-import, and listing.

This is the seam the CLI/API/MCP layers use; they never reach into ingest directly.
"""

import tarfile
import zipfile
from pathlib import Path

from potluck.core.errors import UnknownSourceError, UnsupportedArchiveError
from potluck.ingest.engine import run_import
from potluck.ingest.hashing import file_hash as _file_hash
from potluck.ingest.plugins import ParseContext, detect_source, discover
from potluck.ingest.readers import open_archive
from potluck.models.imports import ImportRun
from potluck.services.context import AppContext
from potluck.storage import imports as _storage_imports


def import_path(ctx: AppContext, path: Path) -> ImportRun:
    """Open the archive at *path*, auto-detect the source plugin, and run the import.

    Returns the completed :class:`~potluck.models.imports.ImportRun` ledger row.

    Raises:
        UnsupportedArchiveError: if *path* is not a recognised archive format,
            or is a corrupt/truncated zip or tar (translated from the stdlib
            errors so interface layers only handle PotluckError).
        UnknownSourceError: if no registered plugin matches the archive contents.

    File hash semantics: single-file archives → sha256 of the passed file;
    multi-part archives → sha256 of the PASSED PART only (not the full set);
    directories → None.

    Detection auto-discovers all registered plugins (via potluck.ingest.sources)
    before scanning the archive.
    """
    # The try spans detection AND parsing: archives are read lazily, so a
    # truncated zip can surface BadZipFile mid-import, not just at open.
    try:
        archive = open_archive(path)

        # detect_source calls discover() internally; no separate call needed here.
        plugin = detect_source(archive)
        if plugin is None:
            registered = ", ".join(sorted(discover())) or "(none)"
            raise UnknownSourceError(
                f"no source plugin recognises '{path}'; registered sources: {registered}"
            )

        fhash: str | None = _file_hash(path) if path.is_file() else None

        parse_ctx = ParseContext(
            attachments_dir=(
                ctx.settings.attachments_dir if ctx.settings.extract_attachments else None
            )
        )

        import_id = run_import(
            ctx.db,
            source_name=plugin.name,
            parser_version=plugin.parser_version,
            drafts=plugin.parse(archive, parse_ctx),
            path=str(path),
            file_hash=fhash,
        )
    except (zipfile.BadZipFile, tarfile.ReadError) as exc:
        raise UnsupportedArchiveError(f"corrupt or unreadable archive: {path}: {exc}") from exc

    with ctx.db.read() as conn:
        return _storage_imports.get_import(conn, import_id)


def list_imports(ctx: AppContext, limit: int = 50) -> list[ImportRun]:
    """Return import runs ordered newest-first, capped at *limit*."""
    with ctx.db.read() as conn:
        return _storage_imports.list_imports(conn, limit)
