"""FTS5 search helpers: query sanitization, SQL composition, ranked execution.

build_search_sql is the single place search SELECTs are rendered: filters are
appended as AND predicates after the MATCH (the FTS index drives the scan;
satellite predicates are PK lookups per candidate — codified by the EXPLAIN
QUERY PLAN test). Without free-text terms the same filters run against items
directly, newest first. Shared items fragments (kind/source/date filters,
newest-first order, text preview) are imported from storage.items — storage
owns items SQL; only FTS-specific SQL (MATCH, bm25, snippet, highlight, the
keyset) plus the emails-satellite from: filter is rendered here.
"""

import re
import sqlite3
from collections.abc import Sequence
from typing import Final

from potluck.models.items import ItemKind, ItemSort
from potluck.storage.fts import BM25_WEIGHT_TEXT, BM25_WEIGHT_TITLE
from potluck.storage.items import LIST_ORDER, TEXT_PREVIEW_SQL, item_filter_predicates

_TOKEN: Final[re.Pattern[str]] = re.compile(r"\w+", re.UNICODE)

_LIKE_SPECIALS: Final[re.Pattern[str]] = re.compile(r"([\\%_])")


def sanitize_query(raw: str, *, prefix: bool = False) -> str | None:
    """User text → safe FTS5 MATCH expression, or None if nothing queryable.

    Extracts \\w+ tokens and double-quotes each, joined by spaces (implicit AND).
    Quoting neutralizes ALL FTS5 operators (AND/OR/NOT/NEAR/:/*/()/^) — raw user
    input can never produce an FTS syntax error.

    ``prefix=True`` is search-as-you-type mode: the LAST token matches as a
    prefix (``"tok"*`` — served by FTS5 term-range scans; dedicated prefix
    indexes were dropped in migration 007 for write speed and index size);
    earlier tokens stay exact.

    Phrase search is NOT supported (v1 semantics): quoted input such as
    ``"foo bar"`` is treated as two independent tokens and matches items
    containing both words anywhere, not the exact sequence.
    """
    tokens = _TOKEN.findall(raw)
    if not tokens:
        return None
    quoted = [f'"{token}"' for token in tokens]
    if prefix:
        quoted[-1] += "*"
    return " ".join(quoted)


def _filter_predicates(
    *,
    kinds: Sequence[ItemKind] | None,
    sources: Sequence[str] | None,
    from_addrs: Sequence[str] | None,
    after_iso: str | None,
    before_iso: str | None,
) -> tuple[list[str], list[object], list[str]]:
    """Shared WHERE fragments for both the MATCH and filter-only variants.

    The kind/source/date fragments come from storage.items (which owns items
    SQL); this module adds only the search-specific parts — the joins those
    fragments need here and the emails-satellite from: filter. Returns
    (predicates, params, joins).
    """
    predicates, params = item_filter_predicates(
        kinds=kinds, sources=sources, after_iso=after_iso, before_iso=before_iso
    )
    joins: list[str] = []

    if sources:
        joins.append("JOIN sources AS s ON s.id = i.source_id")
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
    max_id: int | None = None,
    after_score: float | None = None,
    after_id: int | None = None,
) -> tuple[str, list[object]]:
    """Render one search SELECT.

    Columns: id, kind, title, title_highlight, ts, snippet, score.

    With *match*: BM25-ranked FTS5 search, filters as AND predicates, plus an
    optional keyset cursor — ``max_id`` freezes the candidate set (concurrent
    inserts always get higher rowids) and (after_score, after_id) resumes
    after the last delivered hit. Without *match*: filter-only listing,
    newest first, score 0. The i.id tiebreaker keeps pages stable when scores
    (or timestamps) tie.
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
        cursor_sql = ""
        cursor_params: list[object] = []
        if max_id is not None:
            cursor_sql += " AND i.id <= ?"
            cursor_params.append(max_id)
        if after_score is not None and after_id is not None:
            # Aliases are not usable in WHERE, so bm25() must repeat — but the
            # row-value comparison evaluates it ONCE per candidate row (the
            # expanded OR form costs two more). `>` is correct for the
            # ascending ORDER BY score, i.id (bm25 is negative-better).
            cursor_sql += " AND (bm25(items_fts, ?, ?), i.id) > (?, ?)"
            cursor_params.extend([BM25_WEIGHT_TITLE, BM25_WEIGHT_TEXT, after_score, after_id])
        sql = (
            "SELECT i.id, i.kind, i.title, "
            "highlight(items_fts, 0, '[', ']') AS title_highlight, i.ts, "
            "snippet(items_fts, -1, '[', ']', '…', 12) AS snippet, "
            "bm25(items_fts, ?, ?) AS score "
            "FROM items_fts JOIN items AS i ON i.id = items_fts.rowid"
            f"{join_sql} WHERE items_fts MATCH ?{where_sql}{cursor_sql} "
            "ORDER BY score, i.id LIMIT ? OFFSET ?"
        )
        all_params: list[object] = [BM25_WEIGHT_TITLE, BM25_WEIGHT_TEXT, match]
        all_params.extend(params)
        all_params.extend(cursor_params)
    else:
        sql = (
            "SELECT i.id, i.kind, i.title, NULL AS title_highlight, i.ts, "
            f"COALESCE({TEXT_PREVIEW_SQL}, '') AS snippet, "
            "0.0 AS score "
            f"FROM items AS i{join_sql} WHERE 1=1{where_sql} "
            f"ORDER BY {LIST_ORDER[ItemSort.TS_DESC]} LIMIT ? OFFSET ?"
        )
        all_params = list(params)

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
    max_id: int | None = None,
    after_score: float | None = None,
    after_id: int | None = None,
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
        max_id=max_id,
        after_score=after_score,
        after_id=after_id,
    )
    return conn.execute(sql, params).fetchall()


def current_score(conn: sqlite3.Connection, match: str, rowid: int) -> float | None:
    """The bm25 score document *rowid* has for *match* RIGHT NOW, or None.

    Pagination anchor: corpus-statistics drift (inserts between page fetches)
    re-scores documents, so the cursor's stored score is only a fallback —
    comparing against the anchor's CURRENT score keeps continuation exact.
    """
    row = conn.execute(
        "SELECT bm25(items_fts, ?, ?) FROM items_fts WHERE items_fts MATCH ? AND rowid = ?",
        (BM25_WEIGHT_TITLE, BM25_WEIGHT_TEXT, match, rowid),
    ).fetchone()
    return float(row[0]) if row is not None else None
