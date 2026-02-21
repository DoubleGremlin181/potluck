"""Imports router — upload, file browser, import history, and progress."""

import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import col
from starlette.responses import Response

from potluck.core.logging import get_logger
from potluck.models.base import EntityType, SourceType
from potluck.models.faces import MediaPersonLink
from potluck.models.media import Media, MediaEmbedding
from potluck.models.sources import ImportRun, ImportStatus
from potluck.pipeline import start_ingestion
from potluck.pipeline.ingestion.registry import list_stages
from potluck.pipeline.tasks.ingestion import cancel_ingestion
from potluck.web.dependencies import get_db, require_auth

logger = get_logger("web.imports")

router = APIRouter(prefix="/imports", tags=["imports"], dependencies=[Depends(require_auth)])


@router.get("", response_class=HTMLResponse)
async def imports_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status_filter: str = Query(default="", description="Filter by status"),
    page: int = Query(default=1, ge=1),
) -> Response:
    """Render the imports page with active jobs, upload, and history."""
    per_page = 20

    # Active imports
    active_stmt = (
        select(ImportRun)
        .where(col(ImportRun.status).in_([ImportStatus.PENDING, ImportStatus.RUNNING]))
        .options(selectinload(ImportRun.source))  # type: ignore[arg-type]
        .order_by(col(ImportRun.started_at).desc())
    )
    active_result = await db.execute(active_stmt)
    active_imports = list(active_result.scalars().all())

    # Import history
    history_stmt = (
        select(ImportRun)
        .options(selectinload(ImportRun.source))  # type: ignore[arg-type]
        .order_by(col(ImportRun.started_at).desc())
    )

    if status_filter:
        try:
            history_stmt = history_stmt.where(col(ImportRun.status) == ImportStatus(status_filter))
        except ValueError:
            logger.warning("Ignoring invalid status filter: %s", status_filter)

    count_stmt = select(func.count()).select_from(history_stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    history_stmt = history_stmt.offset((page - 1) * per_page).limit(per_page)
    history_result = await db.execute(history_stmt)
    history = list(history_result.scalars().all())

    # Available ingesters
    stages = list_stages()
    sources = [
        {
            "name": s.SOURCE_TYPE.value.replace("_", " ").title(),
            "source_type": s.SOURCE_TYPE.value,
            "entity_types": [et.value for et in s.SUPPORTED_ENTITY_TYPES],
            "patterns": s.FILENAME_PATTERNS,
        }
        for s in stages
    ]

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/imports.html",
        {
            "active_page": "imports",
            "active_imports": active_imports,
            "history": history,
            "total": total,
            "page": page,
            "per_page": per_page,
            "status_filter": status_filter,
            "sources": sources,
            "statuses": [s.value for s in ImportStatus],
            "entity_types": [et.value for et in EntityType],
        },
    )


@router.get("/active", response_class=HTMLResponse)
async def active_imports_partial(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Return the active imports HTML partial for HTMX polling."""
    stmt = (
        select(ImportRun)
        .where(col(ImportRun.status).in_([ImportStatus.PENDING, ImportStatus.RUNNING]))
        .options(selectinload(ImportRun.source))  # type: ignore[arg-type]
        .order_by(col(ImportRun.started_at).desc())
    )
    result = await db.execute(stmt)
    active_imports = list(result.scalars().all())

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "partials/active_imports.html",
        {"active_imports": active_imports},
    )


def _parse_import_params(
    entity_types: list[str],
    source_type: str,
) -> tuple[list[EntityType] | None, SourceType | None]:
    """Parse shared entity_types and source_type form parameters."""
    types: list[EntityType] | None = None
    if entity_types:
        types = [EntityType(t) for t in entity_types if t]
    st = SourceType(source_type) if source_type else None
    return types, st


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile,
    entity_types: list[str] = Form(default=[]),
    source_type: str = Form(default=""),
    since: str = Form(default=""),
    until: str = Form(default=""),
) -> RedirectResponse:
    """Handle file upload and start ingestion."""
    # Save uploaded file in a temp directory, preserving the original filename.
    # Stage detection (detect_stage) matches path.name against FILENAME_PATTERNS,
    # so the original name must be retained for auto-detection to work.
    tmp_dir = tempfile.mkdtemp(prefix="potluck-upload-")
    original_name = file.filename or "upload"
    tmp_path = Path(tmp_dir) / original_name
    content = await file.read()

    # Reject files larger than 10 GB
    max_size = 10 * 1024 * 1024 * 1024  # 10 GB
    if len(content) > max_size:
        return RedirectResponse(url="/imports?error=file_too_large", status_code=303)

    tmp_path.write_bytes(content)

    types, st = _parse_import_params(entity_types, source_type)

    try:
        task_id, import_run_id = start_ingestion(
            tmp_path,
            types,
            source_type=st,
            since=since or None,
            until=until or None,
        )
    except Exception:
        # Clean up the entire temp directory on failure;
        # on success, the Celery worker handles cleanup after ingestion.
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    return RedirectResponse(url="/imports", status_code=303)


@router.post("/start")
async def start_import_from_path(
    path: str = Form(...),
    entity_types: list[str] = Form(default=[]),
    source_type: str = Form(default=""),
    since: str = Form(default=""),
    until: str = Form(default=""),
) -> RedirectResponse:
    """Start ingestion from a server-side path."""
    file_path = Path(path)
    if not file_path.exists():
        return RedirectResponse(url="/imports?error=path_not_found", status_code=303)

    types, st = _parse_import_params(entity_types, source_type)

    try:
        start_ingestion(
            file_path,
            types,
            source_type=st,
            since=since or None,
            until=until or None,
        )
    except Exception:
        logger.exception("Failed to start ingestion from path=%s", file_path)
        return RedirectResponse(url="/imports?error=ingestion_failed", status_code=303)
    return RedirectResponse(url="/imports", status_code=303)


@router.post("/{run_id}/cancel")
async def cancel_import(run_id: str) -> RedirectResponse:
    """Cancel a running import."""
    result = cancel_ingestion(run_id)
    if not result.get("success"):
        logger.warning("Cancel failed for run %s: %s", run_id, result.get("error"))
        return RedirectResponse(url="/imports?error=cancel_failed", status_code=303)
    return RedirectResponse(url="/imports", status_code=303)


@router.get("/browse")
async def browse_files(
    path: str = Query(default="", description="Directory path"),
) -> JSONResponse:
    """Browse server filesystem for import paths.

    Full filesystem access is intentional: Potluck runs exclusively on
    the user's local machine (or a self-hosted Docker container on their
    LAN) and needs to reach data exports stored anywhere on disk.
    Authentication is handled by the require_auth dependency on the router.
    """
    base_path = Path(path) if path else Path.home()

    if not base_path.exists() or not base_path.is_dir():
        return JSONResponse({"error": "Invalid path", "entries": []})

    entries: list[dict[str, object]] = []
    try:
        for entry in sorted(base_path.iterdir()):
            if entry.name.startswith("."):
                continue
            try:
                size = entry.stat().st_size if entry.is_file() else None
            except OSError:
                size = None
            entries.append(
                {
                    "name": entry.name,
                    "path": str(entry),
                    "is_dir": entry.is_dir(),
                    "size": size,
                }
            )
    except PermissionError:
        return JSONResponse({"error": "Permission denied", "entries": []})

    return JSONResponse(
        {
            "current": str(base_path),
            "parent": str(base_path.parent) if base_path != base_path.parent else None,
            "entries": entries,
        }
    )


@router.get("/{run_id}", response_class=HTMLResponse)
async def import_detail(
    request: Request,
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render detail page for a specific import run."""
    stmt = (
        select(ImportRun).where(col(ImportRun.id) == run_id).options(selectinload(ImportRun.source))  # type: ignore[arg-type]
    )
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail="Import run not found")

    # Processing stats for media entities
    # Count media with various processing stages complete
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

    # Count embeddings and face detections
    embedding_count = (
        await db.execute(select(func.count()).select_from(MediaEmbedding))
    ).scalar() or 0
    face_count = (await db.execute(select(func.count()).select_from(MediaPersonLink))).scalar() or 0

    processing_stats = {
        "media_total": media_total,
        "hashing": media_with_hash,
        "metadata": media_with_metadata,
        "ocr": media_with_ocr,
        "captions": media_with_caption,
        "embeddings": embedding_count,
        "faces": face_count,
    }

    # Duration calculation
    duration = None
    if run.started_at and run.completed_at:
        delta = run.completed_at - run.started_at
        minutes, seconds = divmod(int(delta.total_seconds()), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            duration = f"{hours}h {minutes}m {seconds}s"
        elif minutes:
            duration = f"{minutes}m {seconds}s"
        else:
            duration = f"{seconds}s"

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/import_detail.html",
        {
            "active_page": "imports",
            "run": run,
            "processing_stats": processing_stats,
            "duration": duration,
        },
    )
