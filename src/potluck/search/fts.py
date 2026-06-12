"""FTS5 search helpers: query sanitization and BM25-ranked search."""

import re
import sqlite3
from collections.abc import Sequence
from typing import Final

from potluck.models.items import ItemKind
from potluck.storage.fts import BM25_WEIGHT_TEXT, BM25_WEIGHT_TITLE

_TOKEN: Final[re.Pattern[str]] = re.compile(r"\w+", re.UNICODE)

_SEARCH_SQL_BASE: Final[str] = """
SELECT i.id, i.kind, i.title, i.ts,
       snippet(items_fts, -1, '[', ']', '…', 12) AS snippet,
       bm25(items_fts, ?, ?) AS score
FROM items_fts JOIN items AS i ON i.id = items_fts.rowid
WHERE items_fts MATCH ?
"""

# i.id tiebreaker: equal bm25 scores (trivial with short similar notes) have
# unspecified relative order otherwise, so LIMIT/OFFSET pages could repeat or
# drop rows across requests (same rationale as _LIST_ORDER in storage/items).
_SEARCH_SQL_TAIL: Final[str] = """
ORDER BY score, i.id
LIMIT ? OFFSET ?
"""


def sanitize_query(raw: str) -> str | None:
    """User text → safe FTS5 MATCH expression, or None if nothing queryable.

    Extracts \\w+ tokens and double-quotes each, joined by spaces (implicit AND).
    Quoting neutralizes ALL FTS5 operators (AND/OR/NOT/NEAR/:/*/()/^) — raw user
    input can never produce an FTS syntax error.

    Phrase search is NOT supported (v1 semantics): quoted input such as
    ``"foo bar"`` is treated as two independent tokens and matches items
    containing both words anywhere, not the exact sequence.
    """
    tokens = _TOKEN.findall(raw)
    if not tokens:
        return None
    return " ".join(f'"{token}"' for token in tokens)


def search_items(
    conn: sqlite3.Connection,
    match: str,
    *,
    kinds: Sequence[ItemKind] | None,
    limit: int,
    offset: int,
) -> list[sqlite3.Row]:
    """Execute BM25-ranked FTS5 search and return raw rows.

    Args:
        conn:   Read-only SQLite connection (items_fts must be accessible).
        match:  Pre-sanitized FTS5 MATCH expression (from :func:`sanitize_query`).
        kinds:  Optional allow-list of :class:`~potluck.models.items.ItemKind` values.
        limit:  Maximum number of rows to return.
        offset: Row offset for pagination.

    Returns:
        List of sqlite3.Row objects with columns:
        id, kind, title, ts, snippet, score.
    """
    params: list[object] = [BM25_WEIGHT_TITLE, BM25_WEIGHT_TEXT, match]
    sql = _SEARCH_SQL_BASE

    if kinds is not None and len(kinds) > 0:
        placeholders = ",".join("?" * len(kinds))
        sql += f" AND i.kind IN ({placeholders})"
        params.extend(k.value for k in kinds)

    sql += _SEARCH_SQL_TAIL
    params.extend([limit, offset])

    return conn.execute(sql, params).fetchall()
