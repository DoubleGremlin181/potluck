"""Celery tasks for background ingestion jobs."""

from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from celery.exceptions import Reject, Retry

from potluck.core.celery import celery_app
from potluck.core.logging import get_logger
from potluck.ingesters.utils.progress import IngestionStats, ProgressCallback
from potluck.models.base import EntityType

if TYPE_CHECKING:
    from celery import Task

logger = get_logger(__name__)


# Retry configuration for transient errors
MAX_RETRIES = 3
RETRY_BACKOFF = 60  # seconds
RETRY_BACKOFF_MAX = 600  # 10 minutes


class CeleryProgressCallback(ProgressCallback):
    """Progress callback that updates Celery task state.

    Stores progress in the task's meta field so it can be queried
    via AsyncResult.info.
    """

    def __init__(self, task: "Task"):
        """Initialize with the Celery task instance.

        Args:
            task: The Celery task to update.
        """
        self.task = task

    def on_progress(self, current: int, total: int, percent: float) -> None:
        """Update task state with progress."""
        self.task.update_state(
            state="PROGRESS",
            meta={
                "current": current,
                "total": total,
                "percent": percent,
            },
        )

    def on_file_change(self, filename: str) -> None:
        """Update task state with current file."""
        # Get current meta and update it
        if hasattr(self.task, "request") and self.task.request.id:
            try:
                result = celery_app.AsyncResult(self.task.request.id)
                meta = result.info or {}
                if isinstance(meta, dict):
                    meta["current_file"] = filename
                    self.task.update_state(state="PROGRESS", meta=meta)
            except Exception:
                pass  # Ignore errors in progress updates

    def on_stats_update(self, stats: IngestionStats) -> None:
        """Update task state with statistics."""
        if hasattr(self.task, "request") and self.task.request.id:
            try:
                result = celery_app.AsyncResult(self.task.request.id)
                meta = result.info or {}
                if isinstance(meta, dict):
                    meta["stats"] = {
                        "created": stats.created,
                        "updated": stats.updated,
                        "skipped": stats.skipped,
                        "failed": stats.failed,
                    }
                    self.task.update_state(state="PROGRESS", meta=meta)
            except Exception:
                pass  # Ignore errors in progress updates


def is_transient_error(exc: Exception) -> bool:
    """Check if an exception is a transient error that should be retried.

    Args:
        exc: The exception to check.

    Returns:
        True if the error is transient (DB connection, disk I/O, etc.).
    """
    # Database connection errors
    from sqlalchemy.exc import InterfaceError, OperationalError

    if isinstance(exc, (OperationalError, InterfaceError)):
        return True

    # Disk I/O errors (EIO, ENOSPC, EROFS)
    return isinstance(exc, OSError) and exc.errno in (5, 28, 30)


def is_fatal_error(exc: Exception) -> bool:
    """Check if an exception is a fatal error that should not be retried.

    Args:
        exc: The exception to check.

    Returns:
        True if the error is fatal (missing file, permission denied, etc.).
    """
    if isinstance(exc, FileNotFoundError):
        return True

    return isinstance(exc, PermissionError)


@celery_app.task(  # type: ignore
    bind=True,
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
    reject_on_worker_lost=True,
)
def ingest_file(
    self: "Task",
    import_run_id: str,
    path: str,
    data_types: list[str] | None = None,
) -> dict[str, Any]:
    """Celery task for ingesting a file.

    Args:
        self: Celery task instance (bound).
        import_run_id: UUID of the ImportRun to update.
        path: Path to the file or directory to ingest.
        data_types: Optional list of entity type values to ingest.

    Returns:
        Dict with task result summary.

    Raises:
        Reject: For fatal errors (missing file, permission denied).
        Retry: For transient errors (DB connection, disk I/O).
    """
    from sqlmodel import Session, select

    from potluck.db.session import get_engine
    from potluck.ingesters.coordinator import IngestionCoordinator
    from potluck.models.sources import ImportRun

    logger.info(f"Starting ingestion task for run {import_run_id}")

    # Parse entity types
    entity_types: set[EntityType] | None = None
    if data_types:
        entity_types = {EntityType(dt) for dt in data_types}

    # Create progress callback
    progress_callback = CeleryProgressCallback(self)

    try:
        # Get database session
        engine = get_engine()
        with Session(engine) as session:
            # Get the ImportRun
            stmt = select(ImportRun).where(ImportRun.id == UUID(import_run_id))
            import_run = session.exec(stmt).first()

            if import_run is None:
                logger.error(f"ImportRun not found: {import_run_id}")
                raise Reject(f"ImportRun not found: {import_run_id}", requeue=False)

            # Run ingestion
            coordinator = IngestionCoordinator(
                session=session,
                progress_callback=progress_callback,
            )

            result = coordinator.run(
                path=Path(path),
                entity_types=entity_types,
            )

            # Return summary
            return {
                "import_run_id": str(result.import_run.id),
                "status": result.import_run.status.value,
                "created": result.stats.created,
                "updated": result.stats.updated,
                "skipped": result.stats.skipped,
                "failed": result.stats.failed,
            }

    except Exception as exc:
        logger.exception(f"Ingestion task failed: {exc}")

        # Handle different error types
        if is_fatal_error(exc):
            # Fatal error - mark as failed, don't retry
            _mark_import_failed(import_run_id, str(exc))
            raise Reject(str(exc), requeue=False) from exc

        elif is_transient_error(exc):
            # Transient error - retry with backoff
            logger.warning(f"Transient error, will retry: {exc}")
            raise self.retry(exc=exc) from exc

        else:
            # Unknown error - mark as failed
            _mark_import_failed(import_run_id, str(exc))
            raise Reject(str(exc), requeue=False) from exc


def _mark_import_failed(import_run_id: str, error_message: str) -> None:
    """Mark an ImportRun as failed.

    Args:
        import_run_id: UUID of the ImportRun.
        error_message: Error message to record.
    """
    from sqlmodel import Session, select

    from potluck.db.session import get_engine
    from potluck.models.sources import ImportRun, ImportStatus
    from potluck.models.utils import utc_now

    try:
        engine = get_engine()
        with Session(engine) as session:
            stmt = select(ImportRun).where(ImportRun.id == UUID(import_run_id))
            import_run = session.exec(stmt).first()

            if import_run:
                import_run.status = ImportStatus.FAILED
                import_run.error_message = error_message
                import_run.completed_at = utc_now()
                session.add(import_run)
                session.commit()
    except Exception as e:
        logger.error(f"Failed to mark import as failed: {e}")


@celery_app.task  # type: ignore
def cancel_import(import_run_id: str) -> dict[str, Any]:
    """Cancel a running import.

    Args:
        import_run_id: UUID of the ImportRun to cancel.

    Returns:
        Dict with cancellation result.
    """
    from sqlmodel import Session, select

    from potluck.db.session import get_engine
    from potluck.models.sources import ImportRun, ImportStatus
    from potluck.models.utils import utc_now

    try:
        engine = get_engine()
        with Session(engine) as session:
            stmt = select(ImportRun).where(ImportRun.id == UUID(import_run_id))
            import_run = session.exec(stmt).first()

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
        logger.error(f"Failed to cancel import: {e}")
        return {"success": False, "error": str(e)}


def start_ingestion(
    path: Path,
    entity_types: list[EntityType] | None = None,
) -> tuple[str, str]:
    """Start an ingestion task and return the task and run IDs.

    This is a convenience function for starting ingestion from
    non-Celery code (e.g., web handlers, CLI).

    Args:
        path: Path to ingest.
        entity_types: Optional list of entity types to ingest.

    Returns:
        Tuple of (task_id, import_run_id).
    """
    from sqlmodel import Session

    from potluck.db.session import get_engine
    from potluck.ingesters.utils.dedup import compute_file_hash
    from potluck.models.base import SourceType
    from potluck.models.sources import ImportRun, ImportSource

    # Create import source and run
    engine = get_engine()
    with Session(engine) as session:
        source = ImportSource(
            source_type=SourceType.GENERIC,
            name=path.name,
        )
        session.add(source)
        session.commit()
        session.refresh(source)

        file_hash = None
        if path.is_file():
            file_hash = compute_file_hash(path)

        run = ImportRun(
            source_id=source.id,
            file_hash=file_hash,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        import_run_id = str(run.id)

    # Start Celery task
    data_types = [et.value for et in entity_types] if entity_types else None
    task = ingest_file.delay(import_run_id, str(path), data_types)

    return task.id, import_run_id
