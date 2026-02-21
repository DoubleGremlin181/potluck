"""Entity detail router — generic detail view for any entity type."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from starlette.responses import Response

from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType
from potluck.web.dependencies import get_db, require_auth

router = APIRouter(tags=["entity"], dependencies=[Depends(require_auth)])

# Fields to exclude from generic detail display
_EXCLUDE_FIELDS = {
    "embedding",
    "multimodal_embedding",
    "search_vector",
    "content_hash",
}


@router.get("/entity/{entity_type}/{entity_id}", response_class=HTMLResponse)
async def entity_detail(
    request: Request,
    entity_type: str,
    entity_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render a generic detail page for any entity type."""
    # Redirect person entities to the dedicated people page
    if entity_type == EntityType.PERSON.value:
        return RedirectResponse(url=f"/people/{entity_id}", status_code=303)

    # Look up the entity type
    entity_map = get_entity_type_model_map()
    try:
        matched_type = EntityType(entity_type)
    except ValueError:
        matched_type = None

    if matched_type is None or matched_type not in entity_map:
        raise HTTPException(status_code=404, detail=f"Unknown entity type: {entity_type}")

    model = entity_map[matched_type]

    # Fetch the entity
    stmt = select(model).where(col(model.id) == entity_id)  # type: ignore[attr-defined]
    result = await db.execute(stmt)
    entity = result.scalar_one_or_none()

    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    # Build field list for display
    fields: list[dict[str, object]] = []
    for field_name, field_info in model.model_fields.items():
        if field_name in _EXCLUDE_FIELDS:
            continue
        value = getattr(entity, field_name, None)
        if value is None:
            continue
        fields.append(
            {
                "name": field_name,
                "value": value,
                "description": field_info.description or "",
            }
        )

    display_name = entity_type.replace("_", " ").title()
    text_repr = entity.to_text_repr() if hasattr(entity, "to_text_repr") else ""

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/entity_detail.html",
        {
            "active_page": "",
            "entity": entity,
            "entity_type": entity_type,
            "display_name": display_name,
            "text_repr": text_repr,
            "fields": fields,
        },
    )
