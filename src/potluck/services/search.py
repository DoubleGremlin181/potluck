"""Search service: full-text BM25 search with operators, prefix mode, cursors."""

from potluck.models.search import SearchHit, SearchRequest, SearchResponse
from potluck.search.cursor import SearchCursor, decode_cursor, encode_cursor
from potluck.search.fts import current_score, sanitize_query, search_items
from potluck.search.query import parse_query
from potluck.services.context import AppContext
from potluck.storage.items import dt_to_iso, iso_to_dt


def search(ctx: AppContext, req: SearchRequest) -> SearchResponse:
    """Run a full-text search and return ranked hits.

    Workflow:
    1. ``parse_query`` splits inline operators (from:/source:/kind:/before:/
       after:) out of *req.query*; invalid operator values are silently
       dropped (never an error — search must not fail on user input).
    2. Structured request fields win over inline operators for the same filter.
    3. With free-text terms: BM25-ranked FTS5 MATCH (prefix mode stars the
       last token), filters as predicates. Filters alone: matching items
       newest-first with score 0, paged by offset (no cursor). Neither:
       empty response.

    Pagination: next_cursor freezes the candidate set at the first page's
    MAX(items.id) and resumes after the last delivered hit — re-anchored on
    that hit's CURRENT score, so corpus-statistics drift from concurrent
    inserts cannot repeat or skip pre-existing hits.

    Raises InvalidCursorError for a malformed req.cursor.
    """
    parsed = parse_query(req.query)
    kinds = req.kinds if req.kinds else (list(parsed.kinds) or None)
    sources = req.sources if req.sources else (list(parsed.sources) or None)
    from_addrs = req.from_addrs if req.from_addrs else (list(parsed.from_addrs) or None)
    after = req.after if req.after is not None else parsed.after
    before = req.before if req.before is not None else parsed.before

    match_expr = sanitize_query(parsed.terms, prefix=req.prefix)
    has_filters = any((kinds, sources, from_addrs, after, before))
    if match_expr is None and not has_filters:
        return SearchResponse(query=req.query, hits=[])

    cursor: SearchCursor | None = (
        decode_cursor(req.cursor) if req.cursor is not None and match_expr is not None else None
    )

    with ctx.db.read() as conn:
        max_id: int | None = None
        after_score: float | None = None
        after_id: int | None = None
        if match_expr is not None:
            if cursor is None:
                row = conn.execute("SELECT MAX(id) FROM items").fetchone()
                max_id = int(row[0]) if row[0] is not None else None
            else:
                max_id = cursor.max_id
                # Re-anchor on the last hit's CURRENT score (stored score is
                # the fallback when the anchor no longer matches).
                fresh = current_score(conn, match_expr, cursor.last_id)
                after_score = fresh if fresh is not None else cursor.last_score
                after_id = cursor.last_id

        rows = search_items(
            conn,
            match=match_expr,
            kinds=kinds,
            sources=sources,
            from_addrs=from_addrs,
            after_iso=dt_to_iso(after) if after is not None else None,
            before_iso=dt_to_iso(before) if before is not None else None,
            limit=req.limit,
            offset=req.offset,
            max_id=max_id,
            after_score=after_score,
            after_id=after_id,
        )

    hits = [
        SearchHit(
            id=int(row["id"]),
            kind=row["kind"],
            title=row["title"],
            title_highlight=row["title_highlight"],
            snippet=str(row["snippet"]),
            score=float(row["score"]),
            ts=iso_to_dt(row["ts"]) if row["ts"] is not None else None,
        )
        for row in rows
    ]

    next_cursor: str | None = None
    if match_expr is not None and max_id is not None and len(hits) == req.limit:
        next_cursor = encode_cursor(max_id=max_id, last_score=hits[-1].score, last_id=hits[-1].id)

    return SearchResponse(query=req.query, hits=hits, next_cursor=next_cursor)
