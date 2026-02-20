"""Tags router — CRUD for Tag entities."""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from starlette.responses import Response

from potluck.models.tags import Tag
from potluck.web.dependencies import get_db, require_auth

router = APIRouter(prefix="/tags", tags=["tags"], dependencies=[Depends(require_auth)])


@router.get("", response_class=HTMLResponse)
async def tags_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    q: str = Query(default="", description="Filter tags"),
    page: int = Query(default=1, ge=1),
) -> Response:
    """Render tags list page."""
    per_page = 20

    stmt = select(Tag).order_by(col(Tag.updated_at).desc())

    if q.strip():
        like_q = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                col(Tag.name).ilike(like_q),
                col(Tag.description).ilike(like_q),
            )
        )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(stmt)
    tags = list(result.scalars().all())

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/tags.html",
        {
            "active_page": "tags",
            "tags": tags,
            "total": total,
            "page": page,
            "per_page": per_page,
            "q": q,
        },
    )


@router.post("", response_class=HTMLResponse)
async def create_tag(
    request: Request,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    category: str = Form(default=""),
    description: str = Form(default=""),
) -> RedirectResponse:
    """Create a new Tag."""
    tag = Tag(
        name=name.strip(),
        category=category.strip() or None,
        description=description.strip() or None,
    )
    db.add(tag)
    await db.commit()
    return RedirectResponse(url="/tags", status_code=303)


@router.post("/{tag_id}/edit", response_class=HTMLResponse)
async def edit_tag(
    request: Request,
    tag_id: UUID,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    category: str = Form(default=""),
    description: str = Form(default=""),
) -> RedirectResponse:
    """Update a Tag."""
    stmt = select(Tag).where(col(Tag.id) == tag_id)
    result = await db.execute(stmt)
    tag = result.scalar_one_or_none()

    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")

    tag.name = name.strip()
    tag.category = category.strip() or None
    tag.description = description.strip() or None
    db.add(tag)
    await db.commit()

    return RedirectResponse(url="/tags", status_code=303)


@router.post("/{tag_id}/delete")
async def delete_tag(
    tag_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Delete a Tag and its assignments."""
    stmt = select(Tag).where(col(Tag.id) == tag_id)
    result = await db.execute(stmt)
    tag = result.scalar_one_or_none()

    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")

    await db.delete(tag)
    await db.commit()

    return RedirectResponse(url="/tags", status_code=303)
