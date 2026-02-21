"""Celery tasks for background ingestion jobs."""

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from celery import Task
from celery.exceptions import Reject, Retry
from sqlmodel import Session, select

from potluck.core.celery import (
    MAX_RETRIES,
    RETRY_BACKOFF,
    RETRY_BACKOFF_MAX,
    celery_app,
    is_fatal_error,
    is_transient_error,
)
from potluck.core.logging import get_logger
from potluck.db.session import get_engine
from potluck.models.base import EntityType, SourceType
from potluck.models.sources import ImportRun, ImportSource, ImportStatus
from potluck.models.utils import utc_now
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.orchestrator import PipelineOrchestrator
from potluck.pipeline.utils.hashing import compute_file_hash

logger = get_logger(__name__)


def _mark_import_failed(import_run_id: str, error_message: str) -> None:
    """Mark an ImportRun as failed."""
    try:
        engine = get_engine()
        with Session(engine) as session:
            stmt = select(ImportRun).where(ImportRun.id == UUID(import_run_id))
            result = session.exec(stmt)
            import_run = result.first()
            if import_run:
                import_run.status = ImportStatus.FAILED
                import_run.error_message = error_message
                import_run.completed_at = utc_now()
                session.add(import_run)
                session.commit()
    except Exception:
        logger.exception(
            f"Failed to mark import {import_run_id} as failed with error: {error_message}"
        )


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_ingestion(
    self: "Task[..., dict[str, Any]]",
    import_run_id: str,
    path: str,
    entity_types: list[str] | None = None,
    source_type: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """Celery task for ingesting a file.

    Args:
        self: Celery task instance (bound).
        import_run_id: UUID of the ImportRun to update.
        path: Path to the file or directory to ingest.
        entity_types: Optional list of entity type values to ingest.
        source_type: Optional source type value to override auto-detection.
        since: Optional ISO date string — only ingest entities after this date.
        until: Optional ISO date string — only ingest entities before this date.

    Returns:
        Dict with task result summary.

    Raises:
        Reject: For fatal errors.
        Retry: For transient errors.
    """
    logger.info(f"Starting ingestion task for run {import_run_id}")

    # Parse entity types with validation
    types_to_ingest: set[EntityType] | None = None
    if entity_types:
        try:
            types_to_ingest = {EntityType(et) for et in entity_types}
        except ValueError as e:
            error_msg = f"Invalid entity type in {entity_types}: {e}"
            _mark_import_failed(import_run_id, error_msg)
            raise Reject(error_msg, requeue=False) from e

    # Parse source type override
    source_type_override: SourceType | None = None
    if source_type:
        try:
            source_type_override = SourceType(source_type)
        except ValueError as e:
            error_msg = f"Invalid source type '{source_type}': {e}"
            _mark_import_failed(import_run_id, error_msg)
            raise Reject(error_msg, requeue=False) from e

    # Parse date filters
    filters: PipelineFilter | None = None
    if since or until:
        try:
            filters = PipelineFilter(
                since=datetime.fromisoformat(since) if since else None,
                until=datetime.fromisoformat(until) if until else None,
            )
        except ValueError as e:
            error_msg = f"Invalid date filter (since={since}, until={until}): {e}"
            _mark_import_failed(import_run_id, error_msg)
            raise Reject(error_msg, requeue=False) from e

    # Create progress callback that updates Celery task state
    def on_progress(current: int, total: int, message: str | None) -> None:
        percent = (current / total * 100) if total > 0 else 0
        self.update_state(
            state="PROGRESS",
            meta={
                "current": current,
                "total": total,
                "percent": percent,
                "message": message,
            },
        )

    try:
        engine = get_engine()
        with Session(engine) as session:
            # Load existing ImportRun (created by start_ingestion)
            stmt = select(ImportRun).where(ImportRun.id == UUID(import_run_id))
            result = session.exec(stmt)
            import_run = result.first()
            if import_run is None:
                raise Reject(f"ImportRun not found: {import_run_id}", requeue=False)

            # Load the associated ImportSource
            source_stmt = select(ImportSource).where(ImportSource.id == import_run.source_id)
            import_source = session.exec(source_stmt).first()
            if import_source is None:
                raise Reject(f"ImportSource not found for run {import_run_id}", requeue=False)

            # Run ingestion, passing existing records to avoid duplicates
            orchestrator = PipelineOrchestrator(
                session=session,
                on_progress=on_progress,
            )
            pipeline_result = orchestrator.run(
                path=Path(path),
                entity_types=types_to_ingest,
                filters=filters,
                import_source=import_source,
                import_run=import_run,
                source_type_override=source_type_override,
            )

            return {
                "import_run_id": str(pipeline_result.import_run.id),
                "status": pipeline_result.import_run.status.value,
                "created": pipeline_result.stats.entities_created,
                "updated": pipeline_result.stats.entities_updated,
                "skipped": pipeline_result.stats.entities_skipped,
                "failed": pipeline_result.stats.entities_failed,
            }

    except Reject:
        raise
    except Exception as exc:
        logger.exception(f"Ingestion task failed: {exc}")

        if is_fatal_error(exc):
            _mark_import_failed(import_run_id, str(exc))
            raise Reject(str(exc), requeue=False) from exc
        elif is_transient_error(exc):
            raise self.retry(exc=exc) from exc
        else:
            _mark_import_failed(import_run_id, str(exc))
            raise Reject(str(exc), requeue=False) from exc


@celery_app.task  # type: ignore[untyped-decorator]
def cancel_ingestion(import_run_id: str) -> dict[str, Any]:
    """Cancel a running ingestion.

    Args:
        import_run_id: UUID of the ImportRun to cancel.

    Returns:
        Dict with cancellation result.
    """
    try:
        engine = get_engine()
        with Session(engine) as session:
            stmt = select(ImportRun).where(ImportRun.id == UUID(import_run_id))
            result = session.exec(stmt)
            import_run = result.first()

            if import_run is None:
                return {"success": False, "error": "ImportRun not found"}
            if import_run.is_finished:
                return {"success": False, "error": "Import already finished"}

            import_run.status = ImportStatus.CANCELLED
            import_run.completed_at = utc_now()
            session.add(import_run)
            session.commit()

            return {"success": True, "import_run_id": import_run_id}

    except Exception as e:
        logger.exception(f"Failed to cancel ingestion {import_run_id}")
        return {"success": False, "error": str(e)}


def start_ingestion(
    path: Path,
    entity_types: list[EntityType] | None = None,
    source_type: SourceType | None = None,
    since: str | None = None,
    until: str | None = None,
) -> tuple[str, str]:
    """Start an ingestion task and return task and run IDs.

    Convenience function for starting ingestion from CLI or web handlers.

    Args:
        path: Path to ingest.
        entity_types: Optional list of entity types to ingest.
        source_type: Optional source type override (skips auto-detection).
        since: Optional ISO date string for filtering entities after this date.
        until: Optional ISO date string for filtering entities before this date.

    Returns:
        Tuple of (task_id, import_run_id).
    """
    engine = get_engine()
    with Session(engine) as session:
        source = ImportSource(
            source_type=source_type or SourceType.GENERIC,
            name=path.name,
        )
        session.add(source)
        session.commit()
        session.refresh(source)

        file_hash = compute_file_hash(path) if path.is_file() else None

        run = ImportRun(source_id=source.id, file_hash=file_hash)
        session.add(run)
        session.commit()
        session.refresh(run)

        import_run_id = str(run.id)

    # Start Celery task
    types_list = [et.value for et in entity_types] if entity_types else None
    source_type_str = source_type.value if source_type else None
    task = run_ingestion.delay(
        import_run_id,
        str(path),
        types_list,
        source_type_str,
        since,
        until,
    )

    return task.id, import_run_id
