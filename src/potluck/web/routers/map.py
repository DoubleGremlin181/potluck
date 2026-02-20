"""Map router — Leaflet map view for geolocated entities."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from starlette.responses import Response

from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType
from potluck.web.dependencies import get_db, require_auth

router = APIRouter(prefix="/map", tags=["map"], dependencies=[Depends(require_auth)])

# Entity types that can have location
_GEO_TYPES = {
    EntityType.MEDIA,
    EntityType.LOCATION,
    EntityType.LOCATION_VISIT,
    EntityType.CALENDAR_EVENT,
}


@router.get("", response_class=HTMLResponse)
async def map_page(request: Request) -> Response:
    """Render the map page."""
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/map.html",
        {
            "active_page": "map",
            "entity_types": [et.value for et in _GEO_TYPES],
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
) -> JSONResponse:
    """Return markers for the viewport bounds."""
    entity_map = get_entity_type_model_map()

    target_types = set()
    if types:
        for t in types:
            try:
                et = EntityType(t)
                if et in _GEO_TYPES:
                    target_types.add(et)
            except ValueError:
                pass
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
            .limit(1000)
        )
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
                }
            )

    return JSONResponse({"markers": markers})
