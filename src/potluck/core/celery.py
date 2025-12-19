"""Celery application configuration."""

from celery import Celery

from potluck.core.config import get_settings


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
            "potluck.ingesters.*": {"queue": "ingest"},
            "potluck.processing.*": {"queue": "process"},
            "potluck.embeddings.*": {"queue": "embed"},
        },
    )

    # Auto-discover tasks from potluck packages
    app.autodiscover_tasks(
        [
            "potluck.ingesters",
            "potluck.processing",
            "potluck.embeddings",
        ]
    )

    return app


# Global celery app instance
celery_app = create_celery_app()
