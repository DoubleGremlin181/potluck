"""Search router — hybrid search with filters and browse mode."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from starlette.responses import Response

from potluck.core.exceptions import SearchError
from potluck.core.logging import get_logger
from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType, SourceType
from potluck.search import SearchQuery, SearchResults, search
from potluck.search.dtos import SearchMode
from potluck.web.dependencies import get_db
from potluck.web.utils import parse_entity_types, parse_optional_datetime

logger = get_logger("web.search")

router = APIRouter(tags=["search"])

_BROWSE_PAGE_SIZE = 30


async def _browse_entities(
    db: AsyncSession,
    entity_types: list[EntityType],
    page: int,
    per_page: int,
) -> tuple[list[dict[str, object]], int]:
    """Browse entities by type without a search query.

    Pagination (page/per_page) is applied independently per entity type,
    so the total_count is the sum across all types but each type returns
    up to per_page items.

    Returns (items, total_count).
    """
    entity_map = get_entity_type_model_map()
    items: list[dict[str, object]] = []
    total_count = 0

    for et in entity_types:
        model = entity_map.get(et)
        if model is None:
            continue

        # Count total
        count_stmt = select(func.count()).select_from(model)
        count_result = await db.execute(count_stmt)
        total_count += count_result.scalar() or 0

        # Determine sort column
        if hasattr(model, "occurred_at"):
            order_col = col(model.occurred_at).desc()
        else:
            order_col = col(model.created_at).desc()  # type: ignore[attr-defined]

        stmt = select(model).order_by(order_col).offset((page - 1) * per_page).limit(per_page)
        result = await db.execute(stmt)
        for entity in result.scalars().all():
            items.append(
                {
                    "entity": entity,
                    "entity_type": et.value,
                }
            )

    return items, total_count


@router.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    q: str = Query(default="", description="Search query"),
    mode: str = Query(default="fts", description="Search mode"),
    types: list[str] = Query(default=[], alias="type"),
    source: list[str] = Query(default=[], alias="source"),
    since: str = Query(default="", description="Start date (YYYY-MM-DD)"),
    until: str = Query(default="", description="End date (YYYY-MM-DD)"),
    page: int = Query(default=1, ge=1),
) -> Response:
    """Render search page with results or browse mode."""
    per_page = 20
    results: SearchResults | None = None
    browse_results: list[dict[str, object]] | None = None
    browse_total: int = 0
    error: str | None = None

    if q.strip():
        # Search mode: use the search module
        try:
            search_mode = SearchMode(mode)
        except ValueError:
            search_mode = SearchMode.HYBRID

        entity_types = parse_entity_types(types) or None
        source_types: set[SourceType] | None = None
        if source:
            source_types_set: set[SourceType] = set()
            for s in source:
                try:
                    source_types_set.add(SourceType(s))
                except ValueError:
                    logger.warning("Ignoring invalid source type filter: %s", s)
            source_types = source_types_set or None

        since_dt = parse_optional_datetime(since, field_name="since")
        until_dt = parse_optional_datetime(until, field_name="until")

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

    elif types:
        # Browse mode: show entities of the selected type(s)
        browse_entity_types = list(parse_entity_types(types))
        if browse_entity_types:
            browse_results, browse_total = await _browse_entities(
                db, browse_entity_types, page, _BROWSE_PAGE_SIZE
            )

    templates = request.app.state.templates
    is_htmx = request.headers.get("HX-Request") == "true"

    context: dict[str, object] = {
        "active_page": "search",
        "q": q,
        "mode": mode,
        "selected_types": types,
        "selected_sources": source,
        "since": since,
        "until": until,
        "page": page,
        "per_page": per_page if q.strip() else _BROWSE_PAGE_SIZE,
        "results": results,
        "browse_results": browse_results,
        "browse_total": browse_total,
        "error": error,
        "entity_types": [e.value for e in EntityType],
        "source_types": [s.value for s in SourceType],
        "search_modes": [m.value for m in SearchMode],
    }

    if is_htmx:
        return templates.TemplateResponse(request, "partials/search_results.html", context)  # type: ignore[no-any-return]

    return templates.TemplateResponse(request, "pages/search.html", context)  # type: ignore[no-any-return]
