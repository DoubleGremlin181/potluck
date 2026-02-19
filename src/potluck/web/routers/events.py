"""SSE endpoint for real-time progress updates."""

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from starlette.responses import StreamingResponse

from potluck.core.logging import get_logger
from potluck.db.session import get_async_engine
from potluck.models.sources import ImportRun, ImportStatus
from potluck.web.dependencies import require_auth

logger = get_logger("web.events")

router = APIRouter(tags=["events"], dependencies=[Depends(require_auth)])


async def _progress_stream() -> AsyncGenerator[str, None]:
    """Generate SSE events with import progress data.

    Creates a fresh database session per poll iteration to avoid holding
    a connection pool slot for the lifetime of the SSE stream.
    """
    engine = get_async_engine()
    while True:
        try:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                stmt = select(ImportRun).where(
                    col(ImportRun.status).in_([ImportStatus.PENDING, ImportStatus.RUNNING])
                )
                result = await db.execute(stmt)
                runs = result.scalars().all()

                jobs = [
                    {
                        "id": str(run.id),
                        "status": run.status.value,
                        "progress_current": run.progress_current,
                        "progress_total": run.progress_total,
                        "progress_percent": run.progress_percent,
                        "current_file": run.current_file,
                        "entities_created": run.entities_created,
                        "entities_failed": run.entities_failed,
                    }
                    for run in runs
                ]

                data = json.dumps({"jobs": jobs, "active_count": len(jobs)})
                yield f"event: progress\ndata: {data}\n\n"
        except Exception:
            logger.exception("SSE progress stream: database query failed")
            error_data = json.dumps({"error": "Failed to fetch progress updates"})
            yield f"event: error\ndata: {error_data}\n\n"

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
