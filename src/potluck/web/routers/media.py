"""Media gallery router -- browsing, filtering, and lightbox view.

Supported filters (all combinable, applied via query params):
    - media_type: Restrict to a specific MediaType (image, video, etc.)
    - has_ocr: Show only items with extracted OCR text
    - q: Case-insensitive ILIKE search across caption and OCR text fields

Results are ordered by occurred_at descending (NULLS LAST), then by
created_at descending as a tiebreaker. HTMX partial responses return
only the media grid fragment for infinite-scroll pagination.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from starlette.responses import Response

from potluck.core.logging import get_logger
from potluck.models.faces import MediaPersonLink
from potluck.models.media import Media, MediaEmbedding, MediaType
from potluck.web.dependencies import get_db
from potluck.web.utils import escape_like

logger = get_logger("web.media")

router = APIRouter(prefix="/media", tags=["media"])


@router.get("", response_class=HTMLResponse)
async def media_gallery(
    request: Request,
    db: AsyncSession = Depends(get_db),
    media_type: str = Query(default="", description="Filter by media type"),
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
        try:
            stmt = stmt.where(col(Media.media_type) == MediaType(media_type))
        except ValueError:
            logger.warning("Ignoring invalid media type filter: %s", media_type)

    if has_ocr:
        stmt = stmt.where(col(Media.ocr_text).isnot(None))

    if q.strip():
        like_q = f"%{escape_like(q.strip())}%"
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
    media_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render media detail lightbox content."""
    stmt = select(Media).where(col(Media.id) == media_id)
    result = await db.execute(stmt)
    media = result.scalar_one_or_none()

    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")

    # Fetch embeddings count by type
    emb_stmt = (
        select(col(MediaEmbedding.embedding_type), func.count())
        .where(col(MediaEmbedding.media_id) == media_id)
        .group_by(col(MediaEmbedding.embedding_type))
    )
    emb_result = await db.execute(emb_stmt)
    embeddings = {row[0]: row[1] for row in emb_result.all()}

    # Fetch face detections
    face_stmt = select(MediaPersonLink).where(col(MediaPersonLink.media_id) == media_id)
    face_result = await db.execute(face_stmt)
    faces = list(face_result.scalars().all())

    # Processing status flags
    processing = {
        "has_hash": media.file_hash is not None,
        "has_metadata": media.width is not None,
        "has_ocr": media.ocr_text is not None,
        "has_caption": media.caption is not None,
        "has_embeddings": len(embeddings) > 0,
        "embedding_count": len(embeddings),
        "face_count": len(faces),
    }

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "partials/media_detail.html",
        {
            "media": media,
            "embeddings": embeddings,
            "faces": faces,
            "processing": processing,
        },
    )
