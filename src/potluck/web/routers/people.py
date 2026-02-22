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

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.faces import ClusterStatus, FaceCluster, MediaPersonLink
from potluck.models.people import AliasType, Person, PersonAlias
from potluck.web.dependencies import get_db
from potluck.web.utils import escape_like

logger = get_logger("web.people")

router = APIRouter(prefix="/people", tags=["people"])


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
        like_q = f"%{escape_like(q.strip())}%"
        stmt = stmt.where(col(Person.display_name).ilike(like_q))

    count_stmt = select(func.count()).select_from(
        select(Person)
        .where(col(Person.merged_into_id).is_(None))
        .where(col(Person.display_name).ilike(f"%{escape_like(q.strip())}%") if q.strip() else True)  # type: ignore[arg-type]
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


@router.get("/clusters", response_class=HTMLResponse)
async def face_clusters(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status_filter: str = Query(default="", description="Filter by cluster status"),
    page: int = Query(default=1, ge=1),
) -> Response:
    """Render face clusters page for review and assignment."""
    per_page = 20

    stmt = (
        select(FaceCluster)
        .options(selectinload(FaceCluster.face_links))  # type: ignore[arg-type]
        .order_by(col(FaceCluster.face_count).desc())
    )

    if status_filter:
        try:
            stmt = stmt.where(col(FaceCluster.status) == ClusterStatus(status_filter))
        except ValueError:
            logger.warning("Ignoring invalid cluster status filter: %s", status_filter)

    count_stmt = select(func.count()).select_from(FaceCluster)
    if status_filter:
        try:
            status_enum = ClusterStatus(status_filter)
        except ValueError:
            status_enum = None  # Already logged above
        if status_enum is not None:
            count_stmt = count_stmt.where(col(FaceCluster.status) == status_enum)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(stmt)
    clusters = list(result.scalars().unique().all())

    # Get sample media IDs for each cluster (for thumbnail display)
    cluster_media: dict[str, list[str]] = {}
    for cluster in clusters:
        media_ids = [str(link.media_id) for link in cluster.face_links[:6]]
        cluster_media[str(cluster.id)] = media_ids

    # Get people for assignment dropdown
    people_stmt = (
        select(Person)
        .where(col(Person.merged_into_id).is_(None))
        .order_by(Person.display_name)
        .limit(200)
    )
    people_result = await db.execute(people_stmt)
    people_for_assign = list(people_result.scalars().all())

    # Cluster stats
    pending_count = (
        await db.execute(
            select(func.count())
            .select_from(FaceCluster)
            .where(col(FaceCluster.status) == ClusterStatus.PENDING)
        )
    ).scalar() or 0

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/face_clusters.html",
        {
            "active_page": "people",
            "clusters": clusters,
            "cluster_media": cluster_media,
            "people": people_for_assign,
            "total": total,
            "page": page,
            "per_page": per_page,
            "status_filter": status_filter,
            "statuses": [s.value for s in ClusterStatus],
            "pending_count": pending_count,
        },
    )


@router.post("/clusters/{cluster_id}/assign")
async def assign_cluster(
    cluster_id: UUID,
    db: AsyncSession = Depends(get_db),
    person_id: UUID = Form(...),
) -> RedirectResponse:
    """Assign a face cluster to a person."""
    stmt = select(FaceCluster).where(col(FaceCluster.id) == cluster_id)
    result = await db.execute(stmt)
    cluster = result.scalar_one_or_none()

    if cluster is None:
        raise HTTPException(status_code=404, detail="Cluster not found")

    # Verify person exists
    person_stmt = select(Person).where(col(Person.id) == person_id)
    person = (await db.execute(person_stmt)).scalar_one_or_none()
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")

    cluster.person_id = person_id
    cluster.status = ClusterStatus.CONFIRMED
    db.add(cluster)

    # Also update all MediaPersonLinks in this cluster
    links_stmt = select(MediaPersonLink).where(col(MediaPersonLink.cluster_id) == cluster_id)
    links_result = await db.execute(links_stmt)
    for link in links_result.scalars().all():
        link.person_id = person_id
        link.is_confirmed = True
        db.add(link)

    await db.commit()
    return RedirectResponse(url="/people/clusters", status_code=303)


@router.post("/clusters/{cluster_id}/reject")
async def reject_cluster(
    cluster_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Reject a face cluster as false positive."""
    stmt = select(FaceCluster).where(col(FaceCluster.id) == cluster_id)
    result = await db.execute(stmt)
    cluster = result.scalar_one_or_none()

    if cluster is None:
        raise HTTPException(status_code=404, detail="Cluster not found")

    cluster.status = ClusterStatus.REJECTED
    db.add(cluster)
    await db.commit()
    return RedirectResponse(url="/people/clusters", status_code=303)


@router.get("/{person_id}", response_class=HTMLResponse)
async def person_detail(
    request: Request,
    person_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render person detail page."""
    stmt = (
        select(Person)
        .where(col(Person.id) == person_id)
        .options(
            selectinload(Person.aliases),  # type: ignore[arg-type]
            selectinload(Person.face_encodings),  # type: ignore[arg-type]
        )
    )
    result = await db.execute(stmt)
    person = result.scalar_one_or_none()

    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")

    # Get media where this person appears (via MediaPersonLink)
    face_media_stmt = (
        select(col(MediaPersonLink.media_id))
        .where(col(MediaPersonLink.person_id) == person_id)
        .limit(12)
    )
    face_media_result = await db.execute(face_media_stmt)
    face_media_ids = [str(row[0]) for row in face_media_result.all()]

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/person_detail.html",
        {
            "active_page": "people",
            "person": person,
            "alias_types": [t.value for t in AliasType],
            "face_media_ids": face_media_ids,
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
