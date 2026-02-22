"""Base classes for entity processors.

This module provides the foundation for all processing stages including:
- BaseProcessor abstract base class for all processors
- run_batch_processor_task() shared batch Celery task implementation
- run_batch_stage_task() for chained batch pipeline stages
- Helper utilities for entity persistence
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, ClassVar
from uuid import UUID

from celery import Task
from celery.exceptions import Reject
from sqlmodel import Session, SQLModel, select

from potluck.core.celery import is_fatal_error, is_transient_error
from potluck.core.logging import get_logger
from potluck.db.session import get_engine
from potluck.models import get_entity_type_model_map
from potluck.models.base import EntityType
from potluck.pipeline.base import Stage
from potluck.pipeline.dtos import BatchStageResult, StageResult, StageStatus

logger = get_logger(__name__)


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def _get_entity(session: Session, entity_type: EntityType, entity_id: str) -> SQLModel | None:
    """Fetch an entity by type and ID.

    Args:
        session: Database session.
        entity_type: The entity type to fetch.
        entity_id: The entity ID.

    Returns:
        The entity instance or None if not found.
    """
    model_map = get_entity_type_model_map()
    model_class = model_map.get(entity_type)
    if model_class is None:
        logger.warning(f"No model class found for entity type: {entity_type}")
        return None

    stmt = select(model_class).where(model_class.id == UUID(entity_id))  # type: ignore[attr-defined]
    result = session.exec(stmt)
    return result.one_or_none()


def _get_entities_bulk(
    session: Session,
    entity_type: EntityType,
    entity_ids: list[str],
) -> tuple[dict[str, SQLModel], list[str]]:
    """Fetch multiple entities by type and IDs in a single query.

    Args:
        session: Database session.
        entity_type: The entity type to fetch.
        entity_ids: List of entity IDs to fetch.

    Returns:
        Tuple of (found_entities dict mapping id -> entity, missing_ids list).
    """
    model_map = get_entity_type_model_map()
    model_class = model_map.get(entity_type)
    if model_class is None:
        logger.warning(f"No model class found for entity type: {entity_type}")
        return {}, entity_ids

    uuids = [UUID(eid) for eid in entity_ids]
    stmt = select(model_class).where(model_class.id.in_(uuids))  # type: ignore[attr-defined]
    results = session.exec(stmt).all()

    # Build lookup dict
    found: dict[str, SQLModel] = {str(entity.id): entity for entity in results}  # type: ignore[attr-defined]
    missing = [eid for eid in entity_ids if eid not in found]

    return found, missing


def _update_entity_fields(
    session: Session,
    entity_type: EntityType,
    entity_id: str,
    **fields: Any,
) -> None:
    """Update specific fields on an entity.

    Only updates fields with non-None values to avoid overwriting existing data.

    Args:
        session: Database session.
        entity_type: The entity type.
        entity_id: The entity ID.
        **fields: Fields to update with their new values.
    """
    entity = _get_entity(session, entity_type, entity_id)
    if entity:
        for key, value in fields.items():
            if value is not None:
                setattr(entity, key, value)
        session.add(entity)
        session.commit()


# -----------------------------------------------------------------------------
# Base Processor Class
# -----------------------------------------------------------------------------


class BaseProcessor(Stage[SQLModel, StageResult]):
    """Abstract base class for all entity processors.

    Processors extract information from entities and return structured results.
    Each processor has a NAME that identifies it and declares which entity types
    it supports via SUPPORTED_ENTITY_TYPES.

    Subclasses should either:
    - Set PERSIST_FIELDS to declare which result.data keys map to entity fields
    - Override persist_result() for complex persistence logic (e.g., creating
      related records like MediaPersonLink)

    Attributes:
        NAME: Unique identifier for this processor (must be set by subclasses).
        SUPPORTED_ENTITY_TYPES: Set of EntityType values this processor supports.
        PERSIST_FIELDS: List of result.data keys to persist to entity model.
            Keys are assumed to match entity field names.
    """

    NAME: ClassVar[str]  # Must be set by subclasses
    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]]  # Must be set by subclasses
    PERSIST_FIELDS: ClassVar[list[str]] = []  # Fields to auto-persist from result.data

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Validate required class attributes on subclass definition."""
        super().__init_subclass__(**kwargs)

        # Skip validation for abstract classes
        if getattr(cls, "__abstractmethods__", None):
            return

        if not hasattr(cls, "NAME") or not cls.NAME:
            raise TypeError(f"{cls.__name__} must define NAME class attribute")

        if not hasattr(cls, "SUPPORTED_ENTITY_TYPES"):
            raise TypeError(f"{cls.__name__} must define SUPPORTED_ENTITY_TYPES class attribute")

    @classmethod
    def supports_entity_type(cls, entity_type: EntityType) -> bool:
        """Check if this processor supports a given entity type.

        Args:
            entity_type: The entity type to check.

        Returns:
            True if the processor supports this entity type.
        """
        return entity_type in cls.SUPPORTED_ENTITY_TYPES

    @abstractmethod
    def execute(self, input_data: SQLModel) -> StageResult:
        """Process a single entity.

        Args:
            input_data: The entity to process.

        Returns:
            StageResult with extracted data or error information.
        """
        ...

    def persist_result(
        self,
        session: Session,
        entity_type: EntityType,
        entity_id: str,
        result: StageResult,
    ) -> dict[str, Any]:
        """Persist processing result to database and return task output.

        Default implementation uses PERSIST_FIELDS to update entity model fields.
        Override this method for complex persistence (e.g., creating related
        records like FaceStage creating MediaPersonLink entries).

        Args:
            session: Database session for persistence.
            entity_type: The type of entity being processed.
            entity_id: ID of the entity being processed.
            result: The StageResult from execute().

        Returns:
            Dict suitable for Celery task return value.
        """
        if result.status == StageStatus.COMPLETED and self.PERSIST_FIELDS:
            updates = {field: result.data.get(field) for field in self.PERSIST_FIELDS}
            _update_entity_fields(session, entity_type, entity_id, **updates)

        return {
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "status": result.status.value,
            "processing_time_ms": result.processing_time_ms,
            **{field: result.data.get(field) for field in self.PERSIST_FIELDS},
        }

    def execute_batch(self, entities: list[SQLModel]) -> BatchStageResult:
        """Process a batch of entities.

        Default implementation calls execute() for each item. Subclasses can
        override for optimized batch processing (e.g., EasyOCR batch mode).

        Args:
            entities: List of entities to process.

        Returns:
            BatchStageResult with aggregated statistics and individual results.
        """
        results = [self.execute(e) for e in entities]

        return BatchStageResult(
            stage_name=self.NAME,
            total=len(entities),
            completed=sum(1 for r in results if r.status == StageStatus.COMPLETED),
            failed=sum(1 for r in results if r.status == StageStatus.FAILED),
            skipped=sum(1 for r in results if r.status == StageStatus.SKIPPED),
            results=results,
        )

    def should_execute(self, input_data: SQLModel) -> bool:
        """Check if this entity should be processed.

        Default returns True. Subclasses can override to skip certain entities
        (e.g., OCR processor skipping non-image media).

        Args:
            input_data: The entity to check.

        Returns:
            True if the entity should be processed, False to skip.
        """
        return True


# -----------------------------------------------------------------------------
# Celery Task Runners
# -----------------------------------------------------------------------------


def run_batch_processor_task(
    task: Task[..., dict[str, Any]],
    entity_type: EntityType,
    entity_ids: list[str],
    processor_class: type[BaseProcessor],
) -> dict[str, Any]:
    """Run a processor on a batch of entities with standard error handling.

    This is the core batch task runner shared by all processor tasks. It provides:
    - Single database round-trip for fetching all entities
    - Batch execution via processor.execute_batch() (can be optimized per processor)
    - Bulk persistence of results
    - Aggregated statistics

    Processors can override execute_batch() for vectorized operations (e.g., batched
    model inference) while still benefiting from this shared task infrastructure.

    Args:
        task: The Celery task instance (for retry support).
        entity_type: The type of entities to process.
        entity_ids: List of entity IDs to process.
        processor_class: The processor class to instantiate and run.

    Returns:
        Dict with batch task results including counts and timing.

    Raises:
        Reject: For fatal errors or unsupported entity type.
        Retry: For transient errors (via task.retry).
    """
    processor = processor_class()
    logger.info(
        f"Starting batch {processor.NAME} for {len(entity_ids)} {entity_type.value} entities"
    )

    # Validate processor supports this entity type
    if not processor.supports_entity_type(entity_type):
        raise Reject(
            f"Processor {processor.NAME} does not support entity type {entity_type.value}",
            requeue=False,
        )

    try:
        engine = get_engine()
        with Session(engine) as session:
            # Fetch all entities in a single query
            found_entities, missing_ids = _get_entities_bulk(session, entity_type, entity_ids)
            entities: list[SQLModel] = list(found_entities.values())

            if missing_ids:
                logger.warning(
                    f"Batch {processor.NAME}: {len(missing_ids)} entities not found: "
                    f"{missing_ids[:5]}{'...' if len(missing_ids) > 5 else ''}"
                )

            if not entities:
                return {
                    "entity_type": entity_type.value,
                    "total": len(entity_ids),
                    "completed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "missing": len(missing_ids),
                }

            # Run batch (processor can optimize this)
            batch_result = processor.execute_batch(entities)

            # Persist all successful results
            persisted = 0
            for result in batch_result.results:
                if result.status == StageStatus.COMPLETED and result.item_id:
                    processor.persist_result(session, entity_type, str(result.item_id), result)
                    persisted += 1

            logger.info(
                f"Batch {processor.NAME} complete: "
                f"{batch_result.completed} completed, {batch_result.failed} failed, "
                f"{batch_result.skipped} skipped, {persisted} persisted"
            )

            return {
                "entity_type": entity_type.value,
                "total": batch_result.total,
                "completed": batch_result.completed,
                "failed": batch_result.failed,
                "skipped": batch_result.skipped,
                "missing": len(missing_ids),
            }

    except Reject:
        raise
    except Exception as err:
        logger.exception(f"Batch {processor.NAME} task failed: {err}")
        if is_fatal_error(err):
            raise Reject(str(err), requeue=False) from err
        elif is_transient_error(err):
            raise task.retry(exc=err) from err
        else:
            raise Reject(str(err), requeue=False) from err


def run_batch_stage_task(
    task: Task[..., dict[str, Any]],
    previous_result: dict[str, Any],
    entity_type: EntityType,
    processor_class: type[BaseProcessor],
) -> dict[str, Any]:
    """Run a batch processor stage in a Celery chain, propagating entity IDs.

    This is designed for the batch-by-processor pipeline where each stage receives
    the previous stage's result (containing ``needs_processing`` IDs), processes
    those entities, and returns the same structure for the next stage.

    Args:
        task: The Celery task instance (for retry support).
        previous_result: Return value from the previous stage. Must contain
            a ``needs_processing`` key with a list of entity IDs.
        entity_type: The type of entities to process.
        processor_class: The processor class to instantiate and run.

    Returns:
        Dict with ``entity_type``, ``needs_processing`` (propagated), and stats.
    """
    if not isinstance(previous_result, dict):
        raise Reject(
            f"Batch {processor_class.NAME}: previous_result is not a dict "
            f"(got {type(previous_result).__name__}). Pipeline chain may be misconfigured.",
            requeue=False,
        )

    if "needs_processing" not in previous_result:
        logger.warning(
            f"Batch {processor_class.NAME}: previous_result missing 'needs_processing' key. "
            f"Keys present: {list(previous_result.keys())}. "
            "Previous stage may have failed. Skipping this stage."
        )
        return {
            "entity_type": entity_type.value,
            "needs_processing": [],
            "total": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
        }

    entity_ids: list[str] = previous_result["needs_processing"]

    if not entity_ids:
        logger.info(f"Batch {processor_class.NAME}: no entities to process, skipping")
        return {
            "entity_type": entity_type.value,
            "needs_processing": [],
            "total": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
        }

    result = run_batch_processor_task(task, entity_type, entity_ids, processor_class)

    # Propagate needs_processing for the next stage in the chain
    result["needs_processing"] = entity_ids
    return result
