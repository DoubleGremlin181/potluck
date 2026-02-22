"""Timeline router — scrolling feed of entities ordered by time."""

from collections import OrderedDict
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select
from starlette.responses import Response

from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType
from potluck.web.dependencies import get_db, require_auth
from potluck.web.entity_config import ENTITY_CARD_CONFIG, get_entity_title
from potluck.web.utils import parse_entity_types, parse_optional_datetime

router = APIRouter(prefix="/timeline", tags=["timeline"], dependencies=[Depends(require_auth)])

# Entity types that have a time component
_TIMELINE_TYPES = {
    EntityType.MEDIA,
    EntityType.CHAT_MESSAGE,
    EntityType.EMAIL,
    EntityType.SOCIAL_POST,
    EntityType.SOCIAL_COMMENT,
    EntityType.CALENDAR_EVENT,
    EntityType.TRANSACTION,
    EntityType.LOCATION_VISIT,
    EntityType.BROWSING_HISTORY,
}

_PAGE_SIZE = 50


def _parse_types(types: list[str]) -> set[EntityType]:
    """Parse type filter strings into EntityType set."""
    return parse_entity_types(types, allowed=_TIMELINE_TYPES) or _TIMELINE_TYPES


async def _fetch_timeline_items(
    db: AsyncSession,
    target_types: set[EntityType],
    since_dt: datetime | None,
    until_dt: datetime | None,
    before_dt: datetime | None,
    limit: int,
) -> list[dict[str, object]]:
    """Fetch timeline items across entity types, sorted by occurred_at DESC."""
    entity_map = get_entity_type_model_map()
    items: list[dict[str, object]] = []

    for entity_type in target_types:
        model = entity_map.get(entity_type)
        if model is None or not hasattr(model, "occurred_at"):
            continue

        occurred_at_col = col(model.occurred_at)
        stmt = select(model).where(occurred_at_col.isnot(None))

        if since_dt:
            stmt = stmt.where(occurred_at_col >= since_dt)
        if until_dt:
            stmt = stmt.where(occurred_at_col <= until_dt)
        if before_dt:
            stmt = stmt.where(occurred_at_col < before_dt)

        stmt = stmt.order_by(occurred_at_col.desc()).limit(limit)
        result = await db.execute(stmt)

        for entity in result.scalars().all():
            config = ENTITY_CARD_CONFIG.get(entity_type)
            if config:
                title = get_entity_title(entity, config)
            else:
                title = entity_type.value.replace("_", " ").title()

            items.append(
                {
                    "id": str(entity.id),  # type: ignore[attr-defined]
                    "title": title,
                    "entity_type": entity_type.value,
                    "occurred_at": entity.occurred_at,
                }
            )

    # Sort all items by occurred_at DESC and take the top `limit`
    items.sort(key=lambda x: x["occurred_at"], reverse=True)  # type: ignore[arg-type, return-value]
    return items[:limit]


def _group_by_date(items: list[dict[str, object]]) -> OrderedDict[str, list[dict[str, object]]]:
    """Group items by date string, preserving order."""
    groups: OrderedDict[str, list[dict[str, object]]] = OrderedDict()
    for item in items:
        occurred_at: datetime = item["occurred_at"]  # type: ignore[assignment]
        date_key = occurred_at.strftime("%B %d, %Y")
        groups.setdefault(date_key, []).append(item)
    return groups


@router.get("", response_class=HTMLResponse)
async def timeline_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    types: list[str] = Query(default=[], alias="type"),
    since: str = Query(default=""),
    until: str = Query(default=""),
) -> Response:
    """Render the timeline page with initial items."""
    target_types = _parse_types(types)

    since_dt = parse_optional_datetime(since, field_name="since")
    until_dt = parse_optional_datetime(until, field_name="until")

    items = await _fetch_timeline_items(db, target_types, since_dt, until_dt, None, _PAGE_SIZE)
    date_groups = _group_by_date(items)

    # Determine cursor for next page
    next_before: str | None = None
    if len(items) == _PAGE_SIZE:
        last_item: datetime = items[-1]["occurred_at"]  # type: ignore[assignment]
        next_before = last_item.isoformat()

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/timeline.html",
        {
            "active_page": "timeline",
            "entity_types": [et.value for et in _TIMELINE_TYPES],
            "selected_types": [et.value for et in target_types],
            "since": since,
            "until": until,
            "date_groups": date_groups,
            "next_before": next_before,
            "total_items": len(items),
        },
    )


@router.get("/items", response_class=HTMLResponse)
async def timeline_items(
    request: Request,
    db: AsyncSession = Depends(get_db),
    before: str = Query(description="Cursor: ISO datetime to load items before"),
    types: list[str] = Query(default=[], alias="type"),
    since: str = Query(default=""),
    until: str = Query(default=""),
) -> Response:
    """Return partial HTML of timeline items for HTMX infinite scroll."""
    target_types = _parse_types(types)

    since_dt = parse_optional_datetime(since, field_name="since")
    until_dt = parse_optional_datetime(until, field_name="until")
    before_dt = parse_optional_datetime(before, field_name="before")

    items = await _fetch_timeline_items(db, target_types, since_dt, until_dt, before_dt, _PAGE_SIZE)
    date_groups = _group_by_date(items)

    next_before: str | None = None
    if len(items) == _PAGE_SIZE:
        last_item: datetime = items[-1]["occurred_at"]  # type: ignore[assignment]
        next_before = last_item.isoformat()

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "partials/timeline_items.html",
        {
            "date_groups": date_groups,
            "next_before": next_before,
            "selected_types": [et.value for et in target_types],
            "since": since,
            "until": until,
        },
    )
