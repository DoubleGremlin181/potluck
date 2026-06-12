"""FTS5 search helpers: query sanitization, SQL composition, ranked execution.

build_search_sql is the single place search SELECTs are rendered: filters are
appended as AND predicates after the MATCH (the FTS index drives the scan;
satellite predicates are PK lookups per candidate — codified by the EXPLAIN
QUERY PLAN test). Without free-text terms the same filters run against items
directly, newest first.
"""

import re
import sqlite3
from collections.abc import Sequence
from typing import Final

from potluck.models.items import ItemKind
from potluck.storage.fts import BM25_WEIGHT_TEXT, BM25_WEIGHT_TITLE
from potluck.storage.items import PREVIEW_CHARS

_TOKEN: Final[re.Pattern[str]] = re.compile(r"\w+", re.UNICODE)

_LIKE_SPECIALS: Final[re.Pattern[str]] = re.compile(r"([\\%_])")


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


def _filter_predicates(
    *,
    kinds: Sequence[ItemKind] | None,
    sources: Sequence[str] | None,
    from_addrs: Sequence[str] | None,
    after_iso: str | None,
    before_iso: str | None,
) -> tuple[list[str], list[object], list[str]]:
    """Shared WHERE fragments for both the MATCH and filter-only variants.

    Returns (predicates, params, joins).
    """
    predicates: list[str] = []
    params: list[object] = []
    joins: list[str] = []

    if kinds:
        predicates.append(f"i.kind IN ({','.join('?' * len(kinds))})")
        params.extend(k.value for k in kinds)
    if sources:
        joins.append("JOIN sources AS s ON s.id = i.source_id")
        predicates.append(f"s.name IN ({','.join('?' * len(sources))})")
        params.extend(sources)
    if from_addrs:
        joins.append("JOIN emails AS e ON e.item_id = i.id")
        alternatives: list[str] = []
        for addr in from_addrs:
            if "@" in addr:
                alternatives.append("e.from_addr = ?")
                params.append(addr)
            else:
                # Bare name: prefix match on the addr-spec (escaped for LIKE).
                alternatives.append(r"e.from_addr LIKE ? ESCAPE '\'")
                params.append(_LIKE_SPECIALS.sub(r"\\\1", addr) + "%")
        predicates.append("(" + " OR ".join(alternatives) + ")")
    if after_iso is not None:
        predicates.append("i.ts >= ?")
        params.append(after_iso)
    if before_iso is not None:
        predicates.append("i.ts < ?")
        params.append(before_iso)

    return predicates, params, joins


def build_search_sql(
    *,
    match: str | None,
    kinds: Sequence[ItemKind] | None,
    sources: Sequence[str] | None,
    from_addrs: Sequence[str] | None,
    after_iso: str | None,
    before_iso: str | None,
    limit: int,
    offset: int,
) -> tuple[str, list[object]]:
    """Render one search SELECT (columns: id, kind, title, ts, snippet, score).

    With *match*: BM25-ranked FTS5 search, filters as AND predicates.
    Without: filter-only listing, newest first, score 0. The i.id tiebreaker
    keeps LIMIT/OFFSET pages stable when scores (or timestamps) tie.
    """
    predicates, params, joins = _filter_predicates(
        kinds=kinds,
        sources=sources,
        from_addrs=from_addrs,
        after_iso=after_iso,
        before_iso=before_iso,
    )
    join_sql = (" " + " ".join(joins)) if joins else ""
    where_sql = "".join(f" AND {p}" for p in predicates)

    if match is not None:
        sql = (
            "SELECT i.id, i.kind, i.title, i.ts, "
            "snippet(items_fts, -1, '[', ']', '…', 12) AS snippet, "
            "bm25(items_fts, ?, ?) AS score "
            "FROM items_fts JOIN items AS i ON i.id = items_fts.rowid"
            f"{join_sql} WHERE items_fts MATCH ?{where_sql} "
            "ORDER BY score, i.id LIMIT ? OFFSET ?"
        )
        all_params: list[object] = [BM25_WEIGHT_TITLE, BM25_WEIGHT_TEXT, match]
    else:
        sql = (
            f"SELECT i.id, i.kind, i.title, i.ts, "
            f"COALESCE(substr(i.text, 1, {PREVIEW_CHARS}), '') AS snippet, "
            "0.0 AS score "
            f"FROM items AS i{join_sql} WHERE 1=1{where_sql} "
            "ORDER BY i.ts DESC NULLS LAST, i.id DESC LIMIT ? OFFSET ?"
        )
        all_params = []

    all_params.extend(params)
    all_params.extend([limit, offset])
    return sql, all_params


def search_items(
    conn: sqlite3.Connection,
    *,
    match: str | None,
    kinds: Sequence[ItemKind] | None,
    sources: Sequence[str] | None = None,
    from_addrs: Sequence[str] | None = None,
    after_iso: str | None = None,
    before_iso: str | None = None,
    limit: int,
    offset: int,
) -> list[sqlite3.Row]:
    """Execute one search (see build_search_sql) and return raw rows."""
    sql, params = build_search_sql(
        match=match,
        kinds=kinds,
        sources=sources,
        from_addrs=from_addrs,
        after_iso=after_iso,
        before_iso=before_iso,
        limit=limit,
        offset=offset,
    )
    return conn.execute(sql, params).fetchall()
