"""People router -- person management, aliases, and merging.

Merge strategy: merging sets ``source.merged_into_id`` to the target person
and reassigns all aliases from source to target. Merged persons are excluded
from list queries (filtered by ``merged_into_id IS NULL``) but remain in the
database so that foreign keys from other entities stay valid.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import col
from starlette.responses import Response

from potluck.models.base import SourceType
from potluck.models.people import AliasType, Person, PersonAlias
from potluck.web.dependencies import get_db, require_auth

router = APIRouter(prefix="/people", tags=["people"], dependencies=[Depends(require_auth)])


@router.get("", response_class=HTMLResponse)
async def people_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
) -> Response:
    """Render people list page."""
    per_page = 24
    stmt = (
        select(Person)
        .where(col(Person.merged_into_id).is_(None))
        .options(selectinload(Person.aliases))  # type: ignore[arg-type]
        .order_by(Person.display_name)
    )

    if q.strip():
        like_q = f"%{q.strip()}%"
        stmt = stmt.where(col(Person.display_name).ilike(like_q))

    count_stmt = select(func.count()).select_from(
        select(Person)
        .where(col(Person.merged_into_id).is_(None))
        .where(col(Person.display_name).ilike(f"%{q.strip()}%") if q.strip() else True)  # type: ignore[arg-type]
        .subquery()
    )
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(stmt)
    people = list(result.scalars().unique().all())

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/people.html",
        {
            "active_page": "people",
            "people": people,
            "total": total,
            "page": page,
            "per_page": per_page,
            "q": q,
            "alias_types": [t.value for t in AliasType],
        },
    )


@router.get("/{person_id}", response_class=HTMLResponse)
async def person_detail(
    request: Request,
    person_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render person detail page."""
    stmt = (
        select(Person).where(col(Person.id) == person_id).options(selectinload(Person.aliases))  # type: ignore[arg-type]
    )
    result = await db.execute(stmt)
    person = result.scalar_one_or_none()

    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/person_detail.html",
        {
            "active_page": "people",
            "person": person,
            "alias_types": [t.value for t in AliasType],
        },
    )


@router.post("/{person_id}/alias")
async def add_alias(
    person_id: UUID,
    db: AsyncSession = Depends(get_db),
    alias_type: str = Form(...),
    value: str = Form(...),
) -> RedirectResponse:
    """Add an alias to a person."""
    alias = PersonAlias(
        person_id=person_id,
        alias_type=AliasType(alias_type),
        value=value.strip(),
        source_type=SourceType.MANUAL,
    )
    db.add(alias)
    await db.commit()
    return RedirectResponse(url=f"/people/{person_id}", status_code=303)


@router.post("/merge")
async def merge_people(
    db: AsyncSession = Depends(get_db),
    source_id: UUID = Form(...),
    target_id: UUID = Form(...),
) -> RedirectResponse:
    """Merge source person into target person."""
    if source_id == target_id:
        return RedirectResponse(url=f"/people/{target_id}", status_code=303)

    # Verify both source and target exist
    source_stmt = select(Person).where(col(Person.id) == source_id)
    source = (await db.execute(source_stmt)).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Source person not found")

    target_stmt = select(Person).where(col(Person.id) == target_id)
    target = (await db.execute(target_stmt)).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Target person not found")

    source.merged_into_id = target_id
    db.add(source)

    # Move aliases to target
    alias_stmt = select(PersonAlias).where(col(PersonAlias.person_id) == source_id)
    alias_result = await db.execute(alias_stmt)
    for alias in alias_result.scalars().all():
        alias.person_id = target_id
        db.add(alias)

    await db.commit()

    return RedirectResponse(url=f"/people/{target_id}", status_code=303)
