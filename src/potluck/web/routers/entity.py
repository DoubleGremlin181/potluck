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
from potluck.models.links import EntityLink
from potluck.web.dependencies import get_db, require_auth
from potluck.web.entity_config import ENTITY_CARD_CONFIG, get_entity_title

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

    # Look up card config for this entity type
    config = ENTITY_CARD_CONFIG.get(matched_type)
    display_name = entity_type.replace("_", " ").title()
    title = get_entity_title(entity, config) if config else display_name
    text_repr = entity.to_text_repr() if hasattr(entity, "to_text_repr") else ""

    # Build field lists: key fields (from card config) and remaining fields
    key_field_names = set(config.card_fields) if config else set()
    key_fields: list[dict[str, object]] = []
    other_fields: list[dict[str, object]] = []
    for field_name, field_info in model.model_fields.items():
        if field_name in _EXCLUDE_FIELDS:
            continue
        value = getattr(entity, field_name, None)
        if value is None:
            continue
        field_dict: dict[str, object] = {
            "name": field_name,
            "value": value,
            "description": field_info.description or "",
        }
        if field_name in key_field_names:
            key_fields.append(field_dict)
        else:
            other_fields.append(field_dict)

    # Fetch related entity links
    links_stmt = (
        select(EntityLink)
        .where(
            (
                (col(EntityLink.source_type) == matched_type)
                & (col(EntityLink.source_id) == entity_id)
            )
            | (
                (col(EntityLink.target_type) == matched_type)
                & (col(EntityLink.target_id) == entity_id)
            )
        )
        .limit(20)
    )
    links_result = await db.execute(links_stmt)
    entity_links = list(links_result.scalars().all())

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/entity_detail.html",
        {
            "active_page": "",
            "entity": entity,
            "entity_type": entity_type,
            "display_name": display_name,
            "title": title,
            "text_repr": text_repr,
            "key_fields": key_fields,
            "other_fields": other_fields,
            "entity_links": entity_links,
        },
    )
