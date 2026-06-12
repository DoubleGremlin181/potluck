"""FTS5 external-content index helpers for the items table."""

import sqlite3
from typing import Final

from potluck.core.errors import FtsIntegrityError

BM25_WEIGHT_TITLE: Final[float] = 5.0
BM25_WEIGHT_TEXT: Final[float] = 1.0


def rebuild(conn: sqlite3.Connection) -> None:
    """Rebuild the entire FTS index from the items table.

    Must be called on a write connection (``db.write(fts.rebuild)``) — FTS5
    implements these commands as INSERTs internally; ``db.read()`` connections
    are query_only and will fail with OperationalError.
    """
    conn.execute("INSERT INTO items_fts(items_fts) VALUES ('rebuild')")


def integrity_check(conn: sqlite3.Connection) -> None:
    """Raise FtsIntegrityError if the index disagrees with the items table.

    Must be called on a write connection (``db.write(fts.integrity_check)``) —
    FTS5 implements these commands as INSERTs internally; ``db.read()``
    connections are query_only and will fail with OperationalError.
    """
    try:
        conn.execute("INSERT INTO items_fts(items_fts, rank) VALUES ('integrity-check', 1)")
    except sqlite3.DatabaseError as exc:
        raise FtsIntegrityError(str(exc)) from exc
