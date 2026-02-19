"""Settings router — database stats, config, and maintenance."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from potluck.core.config import get_settings
from potluck.core.logging import get_logger
from potluck.models import get_entity_type_model_map
from potluck.web.dependencies import get_db, require_auth

logger = get_logger("web.settings")

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_auth)])


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
        },
    )
