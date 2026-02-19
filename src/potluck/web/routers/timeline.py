"""Timeline router — interactive timeline visualization."""

import contextlib
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType
from potluck.web.dependencies import get_db, require_auth

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


@router.get("", response_class=HTMLResponse)
async def timeline_page(request: Request) -> Response:
    """Render the timeline page."""
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/timeline.html",
        {
            "active_page": "timeline",
            "entity_types": [et.value for et in _TIMELINE_TYPES],
        },
    )


@router.get("/data")
async def timeline_data(
    db: AsyncSession = Depends(get_db),
    start: str = Query(default="", description="Start date (ISO)"),
    end: str = Query(default="", description="End date (ISO)"),
    types: list[str] = Query(default=[], alias="type"),
) -> JSONResponse:
    """Return timeline items as JSON for vis-timeline."""
    entity_map = get_entity_type_model_map()

    start_dt: datetime | None = None
    end_dt: datetime | None = None
    if start:
        with contextlib.suppress(ValueError):
            start_dt = datetime.fromisoformat(start)
    if end:
        with contextlib.suppress(ValueError):
            end_dt = datetime.fromisoformat(end)

    target_types = set()
    if types:
        for t in types:
            try:
                et = EntityType(t)
                if et in _TIMELINE_TYPES:
                    target_types.add(et)
            except ValueError:
                pass
    else:
        target_types = _TIMELINE_TYPES

    items: list[dict[str, object]] = []

    for entity_type in target_types:
        model = entity_map.get(entity_type)
        if model is None:
            continue

        # Build query for entities with occurred_at
        if not hasattr(model, "occurred_at"):
            continue

        stmt = select(model).where(model.occurred_at.isnot(None))  # type: ignore[attr-defined]

        if start_dt:
            stmt = stmt.where(model.occurred_at >= start_dt)  # type: ignore[attr-defined]
        if end_dt:
            stmt = stmt.where(model.occurred_at <= end_dt)  # type: ignore[attr-defined]

        stmt = stmt.order_by(model.occurred_at).limit(500)  # type: ignore[attr-defined]
        result = await db.execute(stmt)

        for entity in result.scalars().all():
            # Build a title for display
            title = ""
            for attr in ("caption", "subject", "title", "content", "name", "body"):
                val = getattr(entity, attr, None)
                if val:
                    title = str(val)[:80]
                    break
            if not title:
                title = f"{entity_type.value}"

            items.append(
                {
                    "id": str(entity.id),  # type: ignore[attr-defined]
                    "content": title,
                    "start": entity.occurred_at.isoformat(),  # type: ignore[attr-defined]
                    "group": entity_type.value,
                    "className": f"timeline-{entity_type.value.replace('_', '-')}",
                }
            )

    groups = [
        {"id": et.value, "content": et.value.replace("_", " ").title()} for et in target_types
    ]

    return JSONResponse({"items": items, "groups": groups})
