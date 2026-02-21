"""Map router — Leaflet map view for geolocated entities."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from starlette.responses import Response

from potluck.core.logging import get_logger
from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType
from potluck.web.dependencies import get_db, require_auth

logger = get_logger("web.map")

router = APIRouter(prefix="/map", tags=["map"], dependencies=[Depends(require_auth)])

# Entity types that can have location
_GEO_TYPES = {
    EntityType.MEDIA,
    EntityType.LOCATION,
    EntityType.LOCATION_VISIT,
    EntityType.CALENDAR_EVENT,
}

# Fields to extract per entity type: list of (attr, label) tuples.
# Datetime attrs are auto-formatted; strings are truncated at 80 chars.
_EXTRAS_FIELDS: dict[EntityType, list[tuple[str, str]]] = {
    EntityType.LOCATION_VISIT: [
        ("place_name", "Place"),
        ("started_at", "Time"),
        ("duration_minutes", "Duration (min)"),
        ("activity_type", "Activity"),
        ("address", "Address"),
    ],
    EntityType.LOCATION: [
        ("location_type", "Type"),
        ("address", "Address"),
        ("city", "City"),
        ("country", "Country"),
    ],
    EntityType.MEDIA: [
        ("media_type", "Media"),
        ("occurred_at", "Date"),
        ("caption", "Caption"),
    ],
    EntityType.CALENDAR_EVENT: [
        ("summary", "Event"),
        ("start_time", "Start"),
        ("end_time", "End"),
        ("location_text", "Location"),
    ],
}


def _fmt_value(val: object) -> str:
    """Format a value for display in a map popup."""
    if isinstance(val, datetime):
        return val.strftime("%b %d, %Y %H:%M")
    s = str(val)
    if len(s) > 80:
        return s[:77] + "..."
    return s


def _extract_marker_extras(entity: Any, entity_type: EntityType) -> dict[str, str]:
    """Pull type-specific display fields from an entity for map popups."""
    fields = _EXTRAS_FIELDS.get(entity_type, [])
    extras: dict[str, str] = {}
    for attr, label in fields:
        val = getattr(entity, attr, None)
        if val is None or val == "":
            continue
        extras[label] = _fmt_value(val)
    # For media, include the id for thumbnail rendering
    if entity_type == EntityType.MEDIA:
        extras["_media_id"] = str(entity.id)
    return extras


@router.get("", response_class=HTMLResponse)
async def map_page(
    request: Request,
    since: str = Query(default=""),
    until: str = Query(default=""),
) -> Response:
    """Render the map page."""
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/map.html",
        {
            "active_page": "map",
            "entity_types": [et.value for et in _GEO_TYPES],
            "since": since,
            "until": until,
        },
    )


@router.get("/markers")
async def map_markers(
    db: AsyncSession = Depends(get_db),
    south: float = Query(default=-90),
    north: float = Query(default=90),
    west: float = Query(default=-180),
    east: float = Query(default=180),
    types: list[str] = Query(default=[], alias="type"),
    since: str = Query(default=""),
    until: str = Query(default=""),
) -> JSONResponse:
    """Return markers for the viewport bounds."""
    entity_map = get_entity_type_model_map()

    since_dt: datetime | None = None
    until_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            logger.warning("Ignoring invalid 'since' date: %s", since)
    if until:
        try:
            until_dt = datetime.fromisoformat(until)
        except ValueError:
            logger.warning("Ignoring invalid 'until' date: %s", until)

    target_types = set()
    if types:
        for t in types:
            try:
                et = EntityType(t)
                if et in _GEO_TYPES:
                    target_types.add(et)
            except ValueError:
                logger.warning("Ignoring invalid entity type filter: %s", t)
    else:
        target_types = _GEO_TYPES

    markers: list[dict[str, object]] = []

    for entity_type in target_types:
        model = entity_map.get(entity_type)
        if model is None or not (hasattr(model, "latitude") and hasattr(model, "longitude")):
            continue

        stmt = (
            select(model)
            .where(
                col(model.latitude).isnot(None),
                col(model.longitude).isnot(None),
                col(model.latitude) >= south,
                col(model.latitude) <= north,
                col(model.longitude) >= west,
                col(model.longitude) <= east,
            )
            .limit(5000)
        )

        if hasattr(model, "occurred_at"):
            if since_dt is not None:
                stmt = stmt.where(col(model.occurred_at) >= since_dt)
            if until_dt is not None:
                stmt = stmt.where(col(model.occurred_at) <= until_dt)

        result = await db.execute(stmt)

        for entity in result.scalars().all():
            title = ""
            for attr in ("caption", "location_name", "place_name", "name", "subject", "summary"):
                val = getattr(entity, attr, None)
                if val:
                    title = str(val)[:80]
                    break
            if not title:
                title = entity_type.value.replace("_", " ").title()

            markers.append(
                {
                    "id": str(entity.id),  # type: ignore[attr-defined]
                    "lat": entity.latitude,
                    "lng": entity.longitude,
                    "title": title,
                    "type": entity_type.value,
                    "extras": _extract_marker_extras(entity, entity_type),
                }
            )

    return JSONResponse({"markers": markers})
