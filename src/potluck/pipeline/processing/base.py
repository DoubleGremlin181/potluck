"""Base classes for media processors.

This module provides the foundation for all processing stages including:
- BaseProcessor abstract base class for all processors
- run_processor_task() shared Celery task implementation
- Helper utilities for media persistence
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

from celery.exceptions import Reject
from sqlmodel import Session, select

from potluck.core.celery import is_fatal_error, is_transient_error
from potluck.core.logging import get_logger
from potluck.db.session import get_engine
from potluck.models.media import Media
from potluck.pipeline.base import Stage
from potluck.pipeline.dtos import BatchStageResult, StageResult, StageStatus

if TYPE_CHECKING:
    from celery import Task

logger = get_logger(__name__)


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def _get_media(session: Session, media_id: str) -> Media | None:
    """Fetch a Media record by ID."""
    stmt = select(Media).where(Media.id == UUID(media_id))
    result = session.execute(stmt)
    return result.scalar_one_or_none()


def _update_media_fields(session: Session, media_id: str, **fields: Any) -> None:
    """Update specific fields on a Media record.

    Only updates fields with non-None values to avoid overwriting existing data.
    """
    media = _get_media(session, media_id)
    if media:
        for key, value in fields.items():
            if value is not None:
                setattr(media, key, value)
        session.add(media)
        session.commit()


# -----------------------------------------------------------------------------
# Base Processor Class
# -----------------------------------------------------------------------------


class BaseProcessor(Stage[Media, StageResult]):
    """Abstract base class for all media processors.

    Processors extract information from media entities and return structured
    results. Each processor has a NAME that identifies it and implements
    execute() for single item processing.

    Subclasses should either:
    - Set PERSIST_FIELDS to declare which result.data keys map to Media fields
    - Override persist_result() for complex persistence logic (e.g., creating
      related records like MediaPersonLink)

    Attributes:
        NAME: Unique identifier for this processor (must be set by subclasses).
        PERSIST_FIELDS: List of result.data keys to persist to Media model.
            Keys are assumed to match Media field names.
    """

    NAME: ClassVar[str]  # Must be set by subclasses
    PERSIST_FIELDS: ClassVar[list[str]] = []  # Fields to auto-persist from result.data

    @abstractmethod
    def execute(self, media: Media) -> StageResult:
        """Process a single media item.

        Args:
            media: The media item to process.

        Returns:
            StageResult with extracted data or error information.
        """
        ...

    def persist_result(
        self, session: Session, media_id: str, result: StageResult
    ) -> dict[str, Any]:
        """Persist processing result to database and return task output.

        Default implementation uses PERSIST_FIELDS to update Media model fields.
        Override this method for complex persistence (e.g., creating related
        records like FaceStage creating MediaPersonLink entries).

        Args:
            session: Database session for persistence.
            media_id: ID of the media item being processed.
            result: The StageResult from execute().

        Returns:
            Dict suitable for Celery task return value.
        """
        if result.status == StageStatus.COMPLETED and self.PERSIST_FIELDS:
            updates = {field: result.data.get(field) for field in self.PERSIST_FIELDS}
            _update_media_fields(session, media_id, **updates)

        return {
            "media_id": media_id,
            "status": result.status.value,
            "processing_time_ms": result.processing_time_ms,
            **{field: result.data.get(field) for field in self.PERSIST_FIELDS},
        }

    def execute_batch(self, media_items: list[Media]) -> BatchStageResult:
        """Process a batch of media items.

        Default implementation calls execute() for each item. Subclasses can
        override for optimized batch processing (e.g., EasyOCR batch mode).

        Args:
            media_items: List of media items to process.

        Returns:
            BatchStageResult with aggregated statistics and individual results.
        """
        results = [self.execute(m) for m in media_items]

        return BatchStageResult(
            stage_name=self.NAME,
            total=len(media_items),
            completed=sum(1 for r in results if r.status == StageStatus.COMPLETED),
            failed=sum(1 for r in results if r.status == StageStatus.FAILED),
            skipped=sum(1 for r in results if r.status == StageStatus.SKIPPED),
            results=results,
        )

    def should_execute(self, media: Media) -> bool:
        """Check if this media item should be processed.

        Default returns True. Subclasses can override to skip certain media types
        (e.g., OCR processor skipping non-image media).

        Args:
            media: The media item to check.

        Returns:
            True if the item should be processed, False to skip.
        """
        return True


# -----------------------------------------------------------------------------
# Celery Task Runner
# -----------------------------------------------------------------------------


def run_processor_task(
    task: Task[..., dict[str, Any]],
    media_id: str,
    processor_class: type[BaseProcessor],
) -> dict[str, Any]:
    """Execute a processor with standard error handling.

    This is the core implementation shared by all processor tasks. It handles:
    - Media lookup and validation
    - Processor execution
    - Result persistence via processor.persist_result()
    - Error classification (transient vs fatal)
    - Retry/reject logic

    Args:
        task: The Celery task instance (for retry support).
        media_id: ID of the media item to process.
        processor_class: The processor class to instantiate and run.

    Returns:
        Dict with task results from processor.persist_result().

    Raises:
        Reject: For fatal errors or media not found.
        Retry: For transient errors (via task.retry).
    """
    processor = processor_class()
    logger.info(f"Starting {processor.NAME} for media {media_id}")

    try:
        engine = get_engine()
        with Session(engine) as session:
            media = _get_media(session, media_id)
            if media is None:
                raise Reject(f"Media not found: {media_id}", requeue=False)

            result = processor.execute(media)
            return processor.persist_result(session, media_id, result)

    except Reject:
        raise
    except Exception as err:
        logger.exception(f"{processor.NAME} task failed for {media_id}: {err}")
        if is_fatal_error(err):
            raise Reject(str(err), requeue=False) from err
        elif is_transient_error(err):
            raise task.retry(exc=err) from err
        else:
            raise Reject(str(err), requeue=False) from err
