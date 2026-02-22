"""Settings router — database stats, config, and maintenance."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from starlette.responses import Response

from potluck.core.config import get_settings
from potluck.core.logging import get_logger
from potluck.models import get_entity_type_model_map
from potluck.models.faces import FaceCluster, MediaPersonLink
from potluck.models.links import EntityLink
from potluck.models.media import Media, MediaEmbedding
from potluck.models.sources import ImportRun, ImportSource, ImportStatus
from potluck.web.dependencies import get_db

logger = get_logger("web.settings")

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render settings page with DB stats and configuration."""
    entity_map = get_entity_type_model_map()
    settings = get_settings()

    # Table sizes
    table_stats: list[dict[str, object]] = []
    for entity_type, model in entity_map.items():
        stmt = select(func.count()).select_from(model)
        result = await db.execute(stmt)
        count = result.scalar() or 0
        table_stats.append(
            {
                "name": entity_type.value,
                "table": getattr(model, "__tablename__", entity_type.value),
                "count": count,
            }
        )

    table_stats.sort(key=lambda x: x["count"], reverse=True)  # type: ignore[arg-type, return-value]
    total = sum(t["count"] for t in table_stats)  # type: ignore[misc]

    # Database size
    db_size: int | None
    try:
        result = await db.execute(text("SELECT pg_database_size(current_database())"))
        db_size = result.scalar() or 0
    except Exception:
        logger.exception("Failed to retrieve database size")
        db_size = None

    # Processing stats
    media_total = (await db.execute(select(func.count()).select_from(Media))).scalar() or 0
    media_with_caption = (
        await db.execute(
            select(func.count()).select_from(Media).where(col(Media.caption).isnot(None))
        )
    ).scalar() or 0
    media_with_ocr = (
        await db.execute(
            select(func.count()).select_from(Media).where(col(Media.ocr_text).isnot(None))
        )
    ).scalar() or 0
    media_with_hash = (
        await db.execute(
            select(func.count()).select_from(Media).where(col(Media.file_hash).isnot(None))
        )
    ).scalar() or 0
    media_with_metadata = (
        await db.execute(
            select(func.count()).select_from(Media).where(col(Media.width).isnot(None))
        )
    ).scalar() or 0
    media_with_embedding = (
        await db.execute(
            select(func.count(func.distinct(MediaEmbedding.media_id))).select_from(MediaEmbedding)
        )
    ).scalar() or 0

    embedding_count = (
        await db.execute(select(func.count()).select_from(MediaEmbedding))
    ).scalar() or 0
    face_detection_count = (
        await db.execute(select(func.count()).select_from(MediaPersonLink))
    ).scalar() or 0
    face_cluster_count = (
        await db.execute(select(func.count()).select_from(FaceCluster))
    ).scalar() or 0
    entity_link_count = (
        await db.execute(select(func.count()).select_from(EntityLink))
    ).scalar() or 0

    # Import stats
    import_count = (await db.execute(select(func.count()).select_from(ImportRun))).scalar() or 0
    import_source_count = (
        await db.execute(select(func.count()).select_from(ImportSource))
    ).scalar() or 0
    completed_imports = (
        await db.execute(
            select(func.count())
            .select_from(ImportRun)
            .where(col(ImportRun.status) == ImportStatus.COMPLETED)
        )
    ).scalar() or 0
    failed_imports = (
        await db.execute(
            select(func.count())
            .select_from(ImportRun)
            .where(col(ImportRun.status) == ImportStatus.FAILED)
        )
    ).scalar() or 0

    processing_stats = {
        "media_total": media_total,
        "hashing": media_with_hash,
        "metadata": media_with_metadata,
        "ocr": media_with_ocr,
        "captions": media_with_caption,
        "text_embeddings": media_with_embedding,
        "media_embeddings": embedding_count,
        "face_detections": face_detection_count,
        "face_clusters": face_cluster_count,
        "entity_links": entity_link_count,
    }

    import_stats = {
        "total": import_count,
        "sources": import_source_count,
        "completed": completed_imports,
        "failed": failed_imports,
    }

    # Config (sanitized - hide sensitive values)
    config_items = {
        "Database URL": settings.database_url.split("@")[-1]
        if "@" in settings.database_url
        else "configured",
        "Redis URL": settings.redis_url,
        "Web Host": settings.web_host,
        "Web Port": str(settings.web_port),
        "Auth": "Enabled" if settings.web_password else "Disabled",
        "Log Level": settings.log_level,
    }

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/settings.html",
        {
            "active_page": "settings",
            "table_stats": table_stats,
            "total": total,
            "db_size": db_size,
            "config_items": config_items,
            "processing_stats": processing_stats,
            "import_stats": import_stats,
        },
    )
