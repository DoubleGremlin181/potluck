"""SSE endpoint for real-time progress updates."""

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from starlette.responses import StreamingResponse

from potluck.models.sources import ImportRun, ImportStatus
from potluck.web.dependencies import get_db, require_auth

router = APIRouter(tags=["events"], dependencies=[Depends(require_auth)])


async def _progress_stream(db: AsyncSession) -> AsyncGenerator[str, None]:
    """Generate SSE events with import progress data."""
    while True:
        stmt = select(ImportRun).where(
            col(ImportRun.status).in_([ImportStatus.PENDING, ImportStatus.RUNNING])
        )
        result = await db.execute(stmt)
        runs = result.scalars().all()

        jobs = []
        for run in runs:
            jobs.append(
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
            )

        data = json.dumps({"jobs": jobs, "active_count": len(jobs)})
        yield f"event: progress\ndata: {data}\n\n"

        await asyncio.sleep(2)


@router.get("/events/progress")
async def progress_stream(db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    """SSE stream for import progress updates."""
    return StreamingResponse(
        _progress_stream(db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
