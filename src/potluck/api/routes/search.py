"""REST search endpoint: the P2 query language over keyset pagination."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from potluck.api.deps import CtxDep
from potluck.api.errors import error_responses
from potluck.models.items import ItemKind
from potluck.models.search import SearchRequest, SearchResponse
from potluck.services import search as search_service

router = APIRouter(tags=["search"])


@router.get("/search", summary="Full-text search", responses=error_responses(400, 422))
def search(
    ctx: CtxDep,
    q: Annotated[
        str,
        Query(
            max_length=1000,
            description=(
                "Full-text query: free text plus inline operators "
                "(`from:addr`, `source:name`, `kind:email`, `after:YYYY-MM-DD`, "
                "`before:YYYY-MM-DD`). Multi-word queries match items containing "
                "ALL words."
            ),
        ),
    ],
    kind: Annotated[
        list[ItemKind] | None,
        Query(max_length=16, description="Only these item kinds (repeatable)."),
    ] = None,
    source: Annotated[
        list[str] | None,
        Query(
            max_length=64,
            description="Only these source names (repeatable); unknown names match nothing.",
        ),
    ] = None,
    from_addr: Annotated[
        list[str] | None,
        Query(
            max_length=64,
            description="Only emails from these sender addresses (repeatable, case-insensitive).",
        ),
    ] = None,
    after: Annotated[
        datetime | None,
        Query(
            description=(
                "Only items with ts on/after this ISO-8601 date or datetime "
                "(inclusive; naive means UTC)."
            )
        ),
    ] = None,
    before: Annotated[
        datetime | None,
        Query(
            description=(
                "Only items with ts before this ISO-8601 date or datetime "
                "(exclusive; naive means UTC)."
            )
        ),
    ] = None,
    prefix: Annotated[
        bool,
        Query(description="Search-as-you-type: the last query token matches as a prefix."),
    ] = False,
    cursor: Annotated[
        str | None,
        Query(
            description=(
                "Opaque keyset cursor from a previous response's `next_cursor`. "
                "Valid only for the exact same q/prefix/filter set; anything "
                "else is rejected with 400 `invalid_cursor`."
            )
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum hits per page.")] = 20,
) -> SearchResponse:
    """Search the knowledge base with BM25-ranked full-text search.

    The structured query parameters express the same filters as the inline
    operators and WIN over them when both are present. Invalid inline
    operator values are dropped and reported in `warnings` — never an error.
    Hits arrive best-first with bracketed `[match]` snippets.

    **Keyset pagination contract**: when `next_cursor` is non-null, pass it
    back verbatim as `cursor` — with every other parameter unchanged — to
    fetch the next page; a null `next_cursor` means the results are
    exhausted. Cursors are opaque and bound to the exact query/prefix/filter
    set that produced them, and they freeze the result set at the first page
    (items ingested mid-walk appear only in a fresh search), so a walk never
    duplicates or skips hits. A malformed cursor, or one replayed under
    different parameters, yields 400 `invalid_cursor`. Filter-only searches
    (no free text) return matching items newest-first and never produce a
    cursor. Contrast with `/api/items`, which pages by `limit`/`offset`.
    """
    return search_service.search(
        ctx,
        SearchRequest(
            query=q,
            kinds=kind,
            sources=source,
            from_addrs=from_addr,
            after=after,
            before=before,
            prefix=prefix,
            cursor=cursor,
            limit=limit,
        ),
    )
