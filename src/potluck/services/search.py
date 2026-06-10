"""Search service: full-text BM25 search over the items table."""

from potluck.models.search import SearchHit, SearchRequest, SearchResponse
from potluck.search.fts import sanitize_query, search_items
from potluck.services.context import AppContext
from potluck.storage.items import iso_to_dt


def search(ctx: AppContext, req: SearchRequest) -> SearchResponse:
    """Run a full-text search and return ranked hits.

    Workflow:
    1. :func:`~potluck.search.fts.sanitize_query` converts *req.query* to a safe
       FTS5 MATCH expression.  If the query contains no \\w+ tokens, an empty
       :class:`~potluck.models.search.SearchResponse` is returned immediately.
    2. :func:`~potluck.search.fts.search_items` executes the BM25-ranked query on
       a read connection.
    3. Rows are mapped to :class:`~potluck.models.search.SearchHit` DTOs, with
       timestamps converted via :func:`~potluck.storage.items.iso_to_dt`.

    Args:
        ctx: Application context carrying the open database.
        req: Search parameters (query, optional kind filter, limit, offset).

    Returns:
        A :class:`~potluck.models.search.SearchResponse` with the ranked hits.
    """
    match_expr = sanitize_query(req.query)
    if match_expr is None:
        return SearchResponse(query=req.query, hits=[])

    with ctx.db.read() as conn:
        rows = search_items(
            conn,
            match_expr,
            kinds=req.kinds,
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
