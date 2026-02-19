"""Dashboard router — landing page with stats and recent activity."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from starlette.responses import Response

from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType
from potluck.models.sources import ImportRun, ImportStatus
from potluck.web.dependencies import get_db, require_auth

router = APIRouter(tags=["dashboard"], dependencies=[Depends(require_auth)])


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    """Render the dashboard with entity counts, recent activity, and import status."""
    entity_map = get_entity_type_model_map()

    # Count entities per type
    entity_counts: dict[str, int] = {}
    total_entities = 0
    for entity_type, model in entity_map.items():
        stmt = select(func.count()).select_from(model)
        result = await db.execute(stmt)
        count = result.scalar() or 0
        entity_counts[entity_type.value] = count
        total_entities += count

    # Fetch the 3 most recent entities per type, then merge and take the overall top 10
    recent_entities: list[dict[str, object]] = []
    for entity_type, model in entity_map.items():
        if not hasattr(model, "created_at"):
            continue
        stmt = select(model).order_by(col(model.created_at).desc()).limit(3)  # type: ignore[assignment, attr-defined]
        result = await db.execute(stmt)
        for entity in result.scalars().all():
            recent_entities.append(
                {
                    "entity": entity,
                    "entity_type": entity_type.value,
                    "created_at": entity.created_at,  # type: ignore[attr-defined]
                }
            )

    recent_entities.sort(key=lambda x: x["created_at"], reverse=True)  # type: ignore[arg-type, return-value]
    recent_entities = recent_entities[:10]

    # Active imports
    stmt = select(ImportRun).where(  # type: ignore[assignment]
        col(ImportRun.status).in_([ImportStatus.PENDING, ImportStatus.RUNNING])
    )
    result = await db.execute(stmt)
    active_imports = list(result.scalars().all())

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/dashboard.html",
        {
            "active_page": "dashboard",
            "entity_counts": entity_counts,
            "total_entities": total_entities,
            "recent_entities": recent_entities,
            "active_imports": active_imports,
            "entity_types": EntityType,
        },
    )
