"""Search service: full-text BM25 search with inline query operators."""

from potluck.models.search import SearchHit, SearchRequest, SearchResponse
from potluck.search.fts import sanitize_query, search_items
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
    3. With free-text terms: BM25-ranked FTS5 MATCH, filters as predicates.
       Filters alone (no terms): matching items newest-first with score 0.
       Neither: empty response.
    """
    parsed = parse_query(req.query)
    kinds = req.kinds if req.kinds else (list(parsed.kinds) or None)
    sources = req.sources if req.sources else (list(parsed.sources) or None)
    from_addrs = req.from_addrs if req.from_addrs else (list(parsed.from_addrs) or None)
    after = req.after if req.after is not None else parsed.after
    before = req.before if req.before is not None else parsed.before

    match_expr = sanitize_query(parsed.terms)
    has_filters = any((kinds, sources, from_addrs, after, before))
    if match_expr is None and not has_filters:
        return SearchResponse(query=req.query, hits=[])

    with ctx.db.read() as conn:
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
        )

    hits = [
        SearchHit(
            id=int(row["id"]),
            kind=row["kind"],
            title=row["title"],
            snippet=str(row["snippet"]),
            score=float(row["score"]),
            ts=iso_to_dt(row["ts"]) if row["ts"] is not None else None,
        )
        for row in rows
    ]
    return SearchResponse(query=req.query, hits=hits)
