"""SSE endpoint for real-time progress updates."""

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from starlette.responses import StreamingResponse

from potluck.core.logging import get_logger
from potluck.db.session import get_async_engine
from potluck.models.sources import ImportRun, ImportStatus, ProcessingProgress

logger = get_logger("web.events")

router = APIRouter(tags=["events"])


async def _progress_stream() -> AsyncGenerator[str, None]:
    """Generate SSE events with import progress data.

    Creates a fresh database session per poll iteration to avoid holding
    a connection pool slot for the lifetime of the SSE stream.
    """
    engine = get_async_engine()
    consecutive_failures = 0
    max_failures = 5
    while True:
        try:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                stmt = select(ImportRun).where(
                    col(ImportRun.status).in_([ImportStatus.PENDING, ImportStatus.RUNNING])
                )
                result = await db.execute(stmt)
                runs = result.scalars().all()

                # Fetch processing progress for active runs
                run_ids = [run.id for run in runs]
                progress_by_run: dict[str, list[dict[str, object]]] = {}
                if run_ids:
                    progress_stmt = select(ProcessingProgress).where(
                        col(ProcessingProgress.import_run_id).in_(run_ids)
                    )
                    progress_result = await db.execute(progress_stmt)
                    for p in progress_result.scalars().all():
                        rid = str(p.import_run_id)
                        progress_by_run.setdefault(rid, []).append(
                            {
                                "stage_name": p.stage_name,
                                "stage_type": p.stage_type.value,
                                "entity_type": p.entity_type.value,
                                "total": p.total,
                                "completed": p.completed,
                                "failed": p.failed,
                                "status": p.status.value,
                            }
                        )

                jobs = [
                    {
                        "id": str(run.id),
                        "status": str(run.status),
                        "progress_current": run.progress_current,
                        "progress_total": run.progress_total,
                        "progress_percent": run.progress_percent,
                        "current_file": run.current_file,
                        "entities_created": run.entities_created,
                        "entities_skipped": run.entities_skipped,
                        "entities_failed": run.entities_failed,
                        "processing": progress_by_run.get(str(run.id), []),
                    }
                    for run in runs
                ]

                data = json.dumps({"jobs": jobs, "active_count": len(jobs)})
                yield f"event: progress\ndata: {data}\n\n"
                consecutive_failures = 0
        except SQLAlchemyError:
            consecutive_failures += 1
            logger.exception(
                "SSE progress stream: database query failed (%d/%d)",
                consecutive_failures,
                max_failures,
            )
            error_data = json.dumps({"error": "Failed to fetch progress updates"})
            yield f"event: error\ndata: {error_data}\n\n"

            if consecutive_failures >= max_failures:
                fatal_data = json.dumps({"error": "Too many consecutive failures, closing stream"})
                yield f"event: fatal\ndata: {fatal_data}\n\n"
                return

        await asyncio.sleep(2)


@router.get("/events/progress")
async def progress_stream() -> StreamingResponse:
    """SSE stream for import progress updates."""
    return StreamingResponse(
        _progress_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
