"""Search router — hybrid search with filters."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from potluck.core.exceptions import SearchError
from potluck.core.logging import get_logger
from potluck.models.base import EntityType, SourceType
from potluck.search import SearchQuery, SearchResults, search
from potluck.search.dtos import SearchMode
from potluck.web.dependencies import get_db, require_auth

logger = get_logger("web.search")

router = APIRouter(tags=["search"], dependencies=[Depends(require_auth)])


@router.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    q: str = Query(default="", description="Search query"),
    mode: str = Query(default="hybrid", description="Search mode"),
    types: list[str] = Query(default=[], alias="type"),
    source: list[str] = Query(default=[], alias="source"),
    since: str = Query(default="", description="Start date (YYYY-MM-DD)"),
    until: str = Query(default="", description="End date (YYYY-MM-DD)"),
    page: int = Query(default=1, ge=1),
) -> Response:
    """Render search page with results."""
    per_page = 20
    results: SearchResults | None = None
    error: str | None = None

    if q.strip():
        try:
            search_mode = SearchMode(mode)
        except ValueError:
            search_mode = SearchMode.HYBRID

        entity_types: set[EntityType] | None = None
        if types:
            entity_types = {EntityType(t) for t in types if t in EntityType.__members__.values()}

        source_types: set[SourceType] | None = None
        if source:
            source_types = {SourceType(s) for s in source if s in SourceType.__members__.values()}

        since_dt: datetime | None = None
        until_dt: datetime | None = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since)
            except ValueError:
                error = f"Invalid start date format: '{since}'. Use YYYY-MM-DD."
        if until:
            try:
                until_dt = datetime.fromisoformat(until)
            except ValueError:
                error = f"Invalid end date format: '{until}'. Use YYYY-MM-DD."

        if error is None:
            try:
                query = SearchQuery(
                    query=q.strip(),
                    mode=search_mode,
                    entity_types=entity_types,
                    source_types=source_types,
                    limit=per_page,
                    offset=(page - 1) * per_page,
                    since=since_dt,
                    until=until_dt,
                )
                results = await search(query)
            except SearchError as e:
                error = f"Search failed: {e.message}"
            except Exception:
                logger.exception("Unexpected error during search for query=%s", q)
                error = "An unexpected error occurred while searching. Please try again."

    templates = request.app.state.templates
    is_htmx = request.headers.get("HX-Request") == "true"

    context = {
        "active_page": "search",
        "q": q,
        "mode": mode,
        "selected_types": types,
        "selected_sources": source,
        "since": since,
        "until": until,
        "page": page,
        "per_page": per_page,
        "results": results,
        "error": error,
        "entity_types": [e.value for e in EntityType],
        "source_types": [s.value for s in SourceType],
        "search_modes": [m.value for m in SearchMode],
    }

    if is_htmx:
        return templates.TemplateResponse(request, "partials/search_results.html", context)  # type: ignore[no-any-return]

    return templates.TemplateResponse(request, "pages/search.html", context)  # type: ignore[no-any-return]
