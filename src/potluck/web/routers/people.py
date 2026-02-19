"""People router — person management, aliases, and merging."""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import col
from starlette.responses import Response

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
    person_id: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render person detail page."""
    stmt = (
        select(Person)
        .where(col(Person.id) == UUID(person_id))
        .options(selectinload(Person.aliases))  # type: ignore[arg-type]
    )
    result = await db.execute(stmt)
    person = result.scalar_one_or_none()

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
    person_id: str,
    db: AsyncSession = Depends(get_db),
    alias_type: str = Form(...),
    value: str = Form(...),
) -> RedirectResponse:
    """Add an alias to a person."""
    from potluck.models.base import SourceType

    alias = PersonAlias(
        person_id=UUID(person_id),
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
    source_id: str = Form(...),
    target_id: str = Form(...),
) -> RedirectResponse:
    """Merge source person into target person."""
    source_uuid = UUID(source_id)
    target_uuid = UUID(target_id)

    # Get source person
    stmt = select(Person).where(col(Person.id) == source_uuid)
    result = await db.execute(stmt)
    source = result.scalar_one_or_none()

    if source and source_uuid != target_uuid:
        source.merged_into_id = target_uuid
        db.add(source)

        # Move aliases to target
        alias_stmt = select(PersonAlias).where(col(PersonAlias.person_id) == source_uuid)
        alias_result = await db.execute(alias_stmt)
        for alias in alias_result.scalars().all():
            alias.person_id = target_uuid
            db.add(alias)

        await db.commit()

    return RedirectResponse(url=f"/people/{target_id}", status_code=303)
