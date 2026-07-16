"""rm/forget SQL (#153): cascading deletes + the suppressed-hashes registry.

Deletion is mostly pre-wired by the schema: every satellite table and files
has ``item_id ... ON DELETE CASCADE``, and items_fts is external-content with
AFTER DELETE triggers — so ``DELETE FROM items`` keeps everything in sync.
What is NOT automatic:

- ``items.parent_id`` (email reply links) has no ON DELETE action, and links
  can cross imports within one source — a surviving reply pointing at a
  deleted parent would fail the FK check mid-statement. Every delete is
  therefore preceded by ONE set-based parent unlink over the doomed rows.
- The imports ledger doubles as the #126 short-circuit key: deleting an
  import/source removes the ledger rows outright (re-import re-ingests);
  deleting individual items instead clears ``file_hash`` on the owning rows
  so the short-circuit cannot silently keep a removed item gone forever.

Each ``delete_*`` function validates and runs as ONE transaction on the write
connection (single writer thread), so validation cannot race a concurrent
``begin_import`` and a reader can never observe a half-deleted state.
"""

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from potluck.core.errors import (
    ImportNotFoundError,
    ImportRunningError,
    ItemNotFoundError,
    SourceNotFoundError,
)
from potluck.storage.items import dt_to_iso


def suppressed_subset(conn: sqlite3.Connection, hashes: Sequence[str]) -> set[str]:
    """Return the subset of *hashes* present in suppressed_hashes.

    The engine's per-batch anti-join: a single ``IN (...)`` query, mirroring
    ``items.existing_hashes`` (caller guarantees batch-sized input).
    """
    if not hashes:
        return set()
    placeholders = ",".join("?" * len(hashes))
    rows = conn.execute(
        f"SELECT content_hash FROM suppressed_hashes WHERE content_hash IN ({placeholders})",
        list(hashes),
    ).fetchall()
    return {str(row[0]) for row in rows}


def _unlink_children(conn: sqlite3.Connection, items_where: str, params: Sequence[object]) -> None:
    """NULL every parent_id pointing into the doomed item set (one UPDATE).

    Doomed rows' own links are nulled too — harmless, they die in the next
    statement; survivors (email replies in other imports) are the point.
    """
    conn.execute(
        f"UPDATE items SET parent_id = NULL "
        f"WHERE parent_id IN (SELECT id FROM items WHERE {items_where})",
        params,
    )


def _suppress_hashes(conn: sqlite3.Connection, items_where: str, params: Sequence[object]) -> int:
    """Insert the doomed items' content hashes into suppressed_hashes.

    OR IGNORE: already-suppressed hashes (and same-hash twins across sources
    inside the doomed set) do not re-count. Returns newly suppressed rows.
    """
    now = dt_to_iso(datetime.now(UTC))
    cursor = conn.execute(
        f"INSERT OR IGNORE INTO suppressed_hashes (content_hash, suppressed_at) "
        f"SELECT content_hash, ? FROM items WHERE {items_where}",
        [now, *params],
    )
    return cursor.rowcount


def delete_import(conn: sqlite3.Connection, import_id: int, *, forget: bool) -> tuple[int, int]:
    """Delete one import run: its items (cascades do the rest) AND its ledger
    row — which kills the #126 short-circuit, so re-importing the archive
    re-ingests. One transaction; returns (items_deleted, hashes_suppressed).

    Raises:
        ImportNotFoundError: if no import row has this id.
        ImportRunningError: if the run is still 'running' (finish or interrupt
            it first; stale crash leftovers are healed by startup recovery).
    """
    conn.execute("BEGIN")
    try:
        row = conn.execute("SELECT status FROM imports WHERE id = ?", (import_id,)).fetchone()
        if row is None:
            raise ImportNotFoundError(f"no import with id {import_id}")
        if str(row[0]) == "running":
            raise ImportRunningError(
                f"import {import_id} is still running; wait for it to finish before deleting"
            )
        _unlink_children(conn, "import_id = ?", (import_id,))
        suppressed = _suppress_hashes(conn, "import_id = ?", (import_id,)) if forget else 0
        items = conn.execute("DELETE FROM items WHERE import_id = ?", (import_id,)).rowcount
        conn.execute("DELETE FROM imports WHERE id = ?", (import_id,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return items, suppressed


def delete_source(
    conn: sqlite3.Connection, source_name: str, *, forget: bool
) -> tuple[int, int, int]:
    """Delete a source's items and its WHOLE import history (same short-circuit
    reasoning as :func:`delete_import`); the sources row itself survives —
    stable source ids elsewhere. One transaction; returns
    (items_deleted, imports_deleted, hashes_suppressed).

    Raises:
        SourceNotFoundError: if no source with this name exists in the DB.
        ImportRunningError: if any of the source's runs is still 'running'.
    """
    conn.execute("BEGIN")
    try:
        row = conn.execute("SELECT id FROM sources WHERE name = ?", (source_name,)).fetchone()
        if row is None:
            raise SourceNotFoundError(f"no source named '{source_name}' in the database")
        source_id = int(row[0])
        running = conn.execute(
            "SELECT id FROM imports WHERE source_id = ? AND status = 'running' LIMIT 1",
            (source_id,),
        ).fetchone()
        if running is not None:
            raise ImportRunningError(
                f"an import of source '{source_name}' is still running "
                f"(import {int(running[0])}); wait for it to finish before deleting"
            )
        _unlink_children(conn, "source_id = ?", (source_id,))
        suppressed = _suppress_hashes(conn, "source_id = ?", (source_id,)) if forget else 0
        items = conn.execute("DELETE FROM items WHERE source_id = ?", (source_id,)).rowcount
        imports = conn.execute("DELETE FROM imports WHERE source_id = ?", (source_id,)).rowcount
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return items, imports, suppressed


def delete_items(
    conn: sqlite3.Connection, item_ids: Sequence[int], *, forget: bool
) -> tuple[int, int]:
    """Delete individual items and clear ``file_hash`` on their owning import
    rows — without that, the #126 short-circuit would silently prevent a
    removed item from ever returning on re-import of the same archive. A
    cleared file_hash just re-scans: item-level dedup absorbs the rest, so
    plain rm means "may return on re-import" (the rm/forget distinction).
    For forget the clearing is redundant (suppression already blocks return)
    but kept — one code path, and the extra re-parse is the only cost.

    Atomic over the whole id list, one transaction; returns
    (items_deleted, hashes_suppressed).

    Raises:
        ItemNotFoundError: if ANY id is unknown (nothing is deleted).
        ImportRunningError: if an owning import run is still 'running'.
    """
    ids = list(dict.fromkeys(item_ids))  # de-dup, order-preserving
    if not ids:
        return 0, 0
    placeholders = ",".join("?" * len(ids))
    conn.execute("BEGIN")
    try:
        found = {
            int(row[0])
            for row in conn.execute(
                f"SELECT id FROM items WHERE id IN ({placeholders})", ids
            ).fetchall()
        }
        missing = [i for i in ids if i not in found]
        if missing:
            raise ItemNotFoundError(f"no item(s) with id {', '.join(map(str, missing))}")
        running = conn.execute(
            f"""SELECT id FROM imports WHERE status = 'running'
                AND id IN (SELECT DISTINCT import_id FROM items WHERE id IN ({placeholders}))
                LIMIT 1""",
            ids,
        ).fetchone()
        if running is not None:
            raise ImportRunningError(
                f"import {int(running[0])} owning these items is still running; "
                "wait for it to finish before deleting"
            )
        _unlink_children(conn, f"id IN ({placeholders})", ids)
        suppressed = _suppress_hashes(conn, f"id IN ({placeholders})", ids) if forget else 0
        conn.execute(
            f"""UPDATE imports SET file_hash = NULL
                WHERE id IN (SELECT DISTINCT import_id FROM items WHERE id IN ({placeholders}))""",
            ids,
        )
        items = conn.execute(f"DELETE FROM items WHERE id IN ({placeholders})", ids).rowcount
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return items, suppressed
