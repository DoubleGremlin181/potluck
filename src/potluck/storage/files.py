"""files table: per-item attachment metadata rows (no blobs, ever).

All files SQL is owned here; nothing outside storage/ builds files SQL.
"""

import sqlite3
from collections.abc import Sequence
from typing import NamedTuple


class FileRow(NamedTuple):
    """Column values for a files INSERT, ordered to match the column list."""

    item_id: int
    member_path: str
    mime: str | None
    size_bytes: int | None
    sha256: str | None


def delete_files_for_items(conn: sqlite3.Connection, item_ids: Sequence[int]) -> None:
    """Drop the files rows of *item_ids* — one IN(...) statement per batch.

    Used by the update path: an item's attachment set is replaced wholesale.
    """
    if not item_ids:
        return
    placeholders = ",".join("?" * len(item_ids))
    conn.execute(f"DELETE FROM files WHERE item_id IN ({placeholders})", list(item_ids))


def insert_files(conn: sqlite3.Connection, rows: Sequence[FileRow]) -> None:
    """Insert a batch of FileRows via a single ``executemany``."""
    conn.executemany(
        """INSERT INTO files (item_id, member_path, mime, size_bytes, sha256)
           VALUES (?, ?, ?, ?, ?)""",
        rows,
    )


def list_files_for_item(conn: sqlite3.Connection, item_id: int) -> list[sqlite3.Row]:
    """One item's attachment rows in insertion (MIME part) order (#200)."""
    return conn.execute(
        "SELECT member_path, mime, size_bytes, sha256 FROM files WHERE item_id = ? ORDER BY id",
        (item_id,),
    ).fetchall()
