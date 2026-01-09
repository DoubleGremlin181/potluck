"""Celery application configuration and task utilities."""

from celery import Celery
from sqlalchemy.exc import InterfaceError, OperationalError

from potluck.core.config import get_settings

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF = 60  # seconds
RETRY_BACKOFF_MAX = 600  # 10 minutes


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
        # Worker settings
        worker_prefetch_multiplier=1,
        worker_concurrency=4,
        # Task discovery
        task_routes={
            "potluck.pipeline.tasks.ingestion.*": {"queue": "ingest"},
            "potluck.pipeline.tasks.processing.*": {"queue": "process"},
            "potluck.embeddings.*": {"queue": "embed"},
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


# Global celery app instance
celery_app = create_celery_app()
