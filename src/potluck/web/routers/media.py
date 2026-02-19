"""Media gallery router — browsing, filtering, and lightbox view."""

import contextlib
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from starlette.responses import Response

from potluck.models.media import Media, MediaType
from potluck.web.dependencies import get_db, require_auth

router = APIRouter(prefix="/media", tags=["media"], dependencies=[Depends(require_auth)])


@router.get("", response_class=HTMLResponse)
async def media_gallery(
    request: Request,
    db: AsyncSession = Depends(get_db),
    media_type: str = Query(default="", description="Filter by media type"),
    has_faces: bool = Query(default=False, description="Filter by has faces"),
    has_ocr: bool = Query(default=False, description="Filter by has OCR text"),
    q: str = Query(default="", description="Search within captions/OCR"),
    page: int = Query(default=1, ge=1),
) -> Response:
    """Render media gallery with filters."""
    per_page = 30

    stmt = select(Media).order_by(
        col(Media.occurred_at).desc().nullslast(), col(Media.created_at).desc()
    )

    # Filters
    if media_type:
        with contextlib.suppress(ValueError):
            stmt = stmt.where(col(Media.media_type) == MediaType(media_type))

    if has_ocr:
        stmt = stmt.where(col(Media.ocr_text).isnot(None))

    if q.strip():
        like_q = f"%{q.strip()}%"
        stmt = stmt.where((col(Media.caption).ilike(like_q)) | (col(Media.ocr_text).ilike(like_q)))

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # Paginate
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(stmt)
    items = list(result.scalars().all())

    templates = request.app.state.templates
    is_htmx = request.headers.get("HX-Request") == "true"

    context = {
        "active_page": "media",
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "media_type": media_type,
        "has_faces": has_faces,
        "has_ocr": has_ocr,
        "q": q,
        "media_types": [t.value for t in MediaType],
    }

    if is_htmx:
        return templates.TemplateResponse(request, "partials/media_grid.html", context)  # type: ignore[no-any-return]
    return templates.TemplateResponse(request, "pages/media.html", context)  # type: ignore[no-any-return]


@router.get("/{media_id}/detail", response_class=HTMLResponse)
async def media_detail(
    request: Request,
    media_id: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render media detail lightbox content."""
    stmt = select(Media).where(col(Media.id) == UUID(media_id))
    result = await db.execute(stmt)
    media = result.scalar_one_or_none()

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "partials/media_detail.html",
        {"media": media},
    )
