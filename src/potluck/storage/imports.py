"""Imports ledger SQL — all sources/imports reads and writes live here."""

import sqlite3
from datetime import UTC, datetime

from potluck.core.errors import ImportNotFoundError
from potluck.models.imports import ImportRun, ImportStatus
from potluck.storage.items import dt_to_iso, iso_to_dt

# Sane cap for the error column (#132): str(exc) can be pathological; the
# ledger keeps enough to diagnose, never megabytes.
_ERROR_MAX_CHARS = 1000


def ensure_source(conn: sqlite3.Connection, name: str) -> int:
    """Insert the source by name if it does not exist; return its id.

    Two-query pattern (INSERT OR IGNORE + SELECT) is intentional: OR IGNORE
    makes cursor.lastrowid unreliable when the row already exists (returns 0
    on some SQLite versions), so we always do a follow-up SELECT.
    """
    conn.execute("INSERT OR IGNORE INTO sources (name) VALUES (?)", (name,))
    row = conn.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()
    return int(row[0])


def begin_import(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    path: str,
    file_hash: str | None,
    parser_version: int,
    extract_attachments: bool = False,
    items_total: int | None = None,
) -> int:
    """Insert a new import row with status 'running'; return its id.

    items_total is the expected item count when the caller can know it
    cheaply; None (the default) means unknown — progress UIs then show an
    indeterminate bar off items_done alone.
    """
    now = dt_to_iso(datetime.now(UTC))
    cursor = conn.execute(
        """INSERT INTO imports (source_id, path, file_hash, parser_version, started_at,
                                status, extract_attachments, items_total)
           VALUES (?, ?, ?, ?, ?, 'running', ?, ?)""",
        (source_id, path, file_hash, parser_version, now, int(extract_attachments), items_total),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def record_batch(
    conn: sqlite3.Connection,
    import_id: int,
    *,
    new: int,
    duplicate: int,
    updated: int,
    skipped: int,
    suppressed: int = 0,
) -> None:
    """Increment the running counters on an import row."""
    conn.execute(
        """UPDATE imports
           SET items_new        = items_new        + ?,
               items_duplicate  = items_duplicate  + ?,
               items_updated    = items_updated    + ?,
               items_skipped    = items_skipped    + ?,
               items_suppressed = items_suppressed + ?
           WHERE id = ?""",
        (new, duplicate, updated, skipped, suppressed, import_id),
    )


def finish_import(
    conn: sqlite3.Connection,
    import_id: int,
    *,
    status: ImportStatus,
    error: str | None = None,
) -> None:
    """Set finished_at, status, and optional error (truncated sanely) on the row."""
    now = dt_to_iso(datetime.now(UTC))
    if error is not None and len(error) > _ERROR_MAX_CHARS:
        error = error[:_ERROR_MAX_CHARS] + " [truncated]"
    conn.execute(
        "UPDATE imports SET finished_at = ?, status = ?, error = ? WHERE id = ?",
        (now, status, error, import_id),
    )


def fail_stale_running_imports(conn: sqlite3.Connection) -> int:
    """Mark every 'running' import failed with error='interrupted'; return the count.

    Startup recovery (#132): a run left 'running' by a crash or kill can never
    resume (its drafts stream is gone), but its counters still reflect the
    last committed batch — only the status lies. Called when the context
    opens the database, BEFORE any new import can start.
    """
    now = dt_to_iso(datetime.now(UTC))
    cursor = conn.execute(
        """UPDATE imports SET status = 'failed', error = 'interrupted', finished_at = ?
           WHERE status = 'running'""",
        (now,),
    )
    return cursor.rowcount


def _row_to_import_run(row: sqlite3.Row) -> ImportRun:
    finished_raw: str | None = row["finished_at"]
    return ImportRun(
        id=int(row["id"]),
        source=str(row["source_name"]),
        path=str(row["path"]),
        file_hash=row["file_hash"],
        parser_version=int(row["parser_version"]),
        started_at=iso_to_dt(str(row["started_at"])),
        finished_at=iso_to_dt(finished_raw) if finished_raw is not None else None,
        status=row["status"],
        items_new=int(row["items_new"]),
        items_duplicate=int(row["items_duplicate"]),
        items_updated=int(row["items_updated"]),
        items_skipped=int(row["items_skipped"]),
        items_suppressed=int(row["items_suppressed"]),
        items_total=row["items_total"],
        error=row["error"],
        extract_attachments=bool(row["extract_attachments"]),
    )


_BASE_QUERY = (
    "SELECT i.*, s.name AS source_name FROM imports i JOIN sources s ON s.id = i.source_id"
)


def get_import(conn: sqlite3.Connection, import_id: int) -> ImportRun:
    """Return a fully-hydrated ImportRun for the given import id.

    Raises:
        ImportNotFoundError: if no import row has this id.
    """
    row = conn.execute(
        f"{_BASE_QUERY} WHERE i.id = ?",
        (import_id,),
    ).fetchone()
    if row is None:
        raise ImportNotFoundError(f"no import with id {import_id}")
    return _row_to_import_run(row)


def list_imports(conn: sqlite3.Connection, limit: int = 50, offset: int = 0) -> list[ImportRun]:
    """Return one page of import runs, newest first."""
    rows = conn.execute(
        f"{_BASE_QUERY} ORDER BY i.id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [_row_to_import_run(row) for row in rows]


def count_imports(conn: sqlite3.Connection) -> int:
    """Total number of import runs (the unpaginated history size)."""
    return int(conn.execute("SELECT count(*) FROM imports").fetchone()[0])


def find_completed_import(
    conn: sqlite3.Connection,
    *,
    source_name: str,
    file_hash: str,
    parser_version: int,
    extract_attachments: bool = False,
) -> ImportRun | None:
    """Latest COMPLETED run of *source_name* over this exact archive content.

    The (file_hash, parser_version) key drives the no-op short-circuit (#126):
    same bytes + same parser means re-parsing cannot change anything; a parser
    bump misses the key and re-ingests naturally. Failed/running rows never
    match.

    Parse side effects are settings-dependent (#198 review 9): when the
    current request wants attachment extraction, only a prior run that also
    extracted can short-circuit it (the superset rule — a prior extraction
    run covers a non-extraction request, never vice versa).
    """
    row = conn.execute(
        f"""{_BASE_QUERY}
            WHERE s.name = ? AND i.file_hash = ? AND i.parser_version = ?
              AND i.status = 'completed'
              AND (? = 0 OR i.extract_attachments = 1)
            ORDER BY i.id DESC LIMIT 1""",
        (source_name, file_hash, parser_version, int(extract_attachments)),
    ).fetchone()
    return _row_to_import_run(row) if row is not None else None
