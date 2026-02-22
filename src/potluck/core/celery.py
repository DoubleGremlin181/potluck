"""Celery application configuration and task utilities."""

from celery import Celery
from celery.signals import task_postrun
from sqlalchemy.exc import InterfaceError, OperationalError

from potluck.core.config import get_settings
from potluck.core.logging import get_logger

logger = get_logger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF = 60  # seconds
RETRY_BACKOFF_MAX = 600  # 10 minutes

# Pipeline priority constants (lower number = higher priority)
PRIORITY_INGEST = 0
PRIORITY_LINK = 9
_PRIORITY_STEPS = list(range(10))  # 0-9


def processor_to_celery_priority(processor_priority: int) -> int:
    """Map a processor registry priority to a Celery queue priority.

    Processor priorities (10, 20, 30, ...) are divided by 10 and clamped
    to the range [1, 8]. Priority 0 is reserved for ingestion, 9 for linking.

    Args:
        processor_priority: Processor priority from the registry (e.g. 10, 20, 50).

    Returns:
        Celery priority integer in range [1, 8].
    """
    return max(1, min(processor_priority // 10, 8))


def is_transient_error(error: Exception) -> bool:
    """Check if exception is transient and should be retried.

    Transient errors include:
    - Database connection issues (OperationalError, InterfaceError)
    - Disk I/O errors (EIO, ENOSPC, EROFS)

    Args:
        error: The exception to check.

    Returns:
        True if the error is transient and the operation should be retried.
    """
    if isinstance(error, OperationalError | InterfaceError):
        return True
    # Disk I/O errors (EIO, ENOSPC, EROFS)
    return isinstance(error, OSError) and error.errno in (5, 28, 30)


def is_fatal_error(error: Exception) -> bool:
    """Check if exception is fatal and should not be retried.

    Fatal errors include:
    - FileNotFoundError (file is missing, won't appear on retry)
    - PermissionError (access denied, won't change on retry)

    Args:
        error: The exception to check.

    Returns:
        True if the error is fatal and the task should be rejected.
    """
    return isinstance(error, FileNotFoundError | PermissionError)


def create_celery_app() -> Celery:
    """Create and configure the Celery application.

    Returns:
        Configured Celery application instance.
    """
    settings = get_settings()

    app = Celery(
        "potluck",
        broker=str(settings.redis_url),
        backend=str(settings.redis_url),
    )

    # Celery configuration
    app.conf.update(
        # Task settings
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        # Task execution settings
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        # Result settings
        result_expires=3600,  # 1 hour
        # Worker settings — concurrency=1 ensures only one model type is loaded
        # at a time when using batch-by-processor processing
        worker_prefetch_multiplier=1,
        worker_concurrency=1,
        # Single pipeline queue with 10 priority levels (0=highest, 9=lowest).
        # With concurrency=1, tasks execute in strict priority order:
        #   0=ingest, 1-8=processing stages, 9=linking
        broker_transport_options={"priority_steps": _PRIORITY_STEPS},
        task_routes={
            "potluck.pipeline.tasks.ingestion.*": {"queue": "pipeline"},
            "potluck.pipeline.tasks.processing.*": {"queue": "pipeline"},
        },
    )

    # Auto-discover tasks from potluck packages
    app.autodiscover_tasks(
        [
            "potluck.pipeline.tasks",
            "potluck.embeddings",
        ]
    )

    return app


def has_pending_processing(app: Celery) -> bool:
    """Check if any processing tasks (priorities 1-8) are pending in the pipeline queue.

    Used by linker tasks as a preemption guard: if processing work remains,
    the linker re-queues itself with a countdown to avoid running before
    all processing completes. This is only needed when running with multiple
    workers (concurrency > 1); with concurrency=1, priority ordering is strict.

    Args:
        app: The Celery application instance.

    Returns:
        True if any processing tasks are pending.
    """
    try:
        with app.connection_for_read() as conn:
            redis = conn.channel().client
            sep = "\x06\x16"
            for p in range(1, PRIORITY_LINK):
                key = f"pipeline{sep}{p}"
                if redis.llen(key) > 0:
                    return True
        return False
    except Exception:
        logger.warning("Failed to check pending processing tasks, assuming still pending")
        return True


# Global celery app instance
celery_app = create_celery_app()


@task_postrun.connect  # type: ignore[untyped-decorator]
def cleanup_models_after_task(**kwargs: object) -> None:
    """Unload all ML models from memory after each task completes.

    Combined with worker_concurrency=1, this ensures only one model type is in
    memory at a time when processing batches sequentially. Each batch task loads
    its model, processes entities, then this signal handler unloads it before
    the next task starts.

    This fires after every task, not just processing tasks. It is a no-op when
    no models are loaded.
    """
    try:
        # Deferred import: celery.py → processing.core.ml → processing.__init__
        # → processors → celery_app (circular). All ML deps are always available.
        from potluck.pipeline.processing.core.ml import MLModels

        MLModels.unload_all()
    except Exception:
        logger.exception("Failed to unload ML models after task. Memory may not be freed.")
