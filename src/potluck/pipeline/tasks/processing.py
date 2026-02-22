"""Celery task orchestration for processing pipeline.

This module provides:
- run_batch_entity_pipeline(): Queue batch-by-processor pipeline for a group of entities
- run_entity_pipeline(): Queue pipeline for a single entity (wraps batch with [id])
- Per-linker Celery tasks + dispatch helpers for temporal, spatial, semantic linking

Processing Architecture:
    Entities are processed in batches grouped by entity type. Each batch stage loads
    ONE model, processes ALL eligible entities, then unloads. This keeps peak memory
    at the size of the largest single model (~460MB Florence-2) instead of all models
    simultaneously (~6.3GB).

    All tasks run on a single ``pipeline`` queue with 10 priority levels (0-9).
    With concurrency=1, this ensures strict ordering: ingestion (0) → processing
    stages (1-8) → linking (9).

Auto-discovery: Importing the processing module triggers automatic discovery
and registration of all processor tasks via pkgutil.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from celery import Task, chain
from celery.exceptions import Reject, Retry
from sqlmodel import Session

# Import processing module to trigger auto-discovery of all processor tasks
import potluck.pipeline.processing  # noqa: F401
from potluck.core.celery import (
    MAX_RETRIES,
    PRIORITY_LINK,
    RETRY_BACKOFF,
    RETRY_BACKOFF_MAX,
    celery_app,
    has_pending_processing,
    is_transient_error,
    processor_to_celery_priority,
)
from potluck.core.logging import get_logger
from potluck.models.base import EntityType

# Import registry for dynamic pipeline construction
from potluck.pipeline.processing.core.registry import ProcessorRegistry

logger = get_logger(__name__)

# Countdown in seconds before a linker retries when processing is still pending
_LINKER_REQUEUE_COUNTDOWN = 30


def run_batch_entity_pipeline(
    entity_type_str: str, entity_ids: list[str], import_run_id: str | None = None
) -> None:
    """Queue batch-by-processor pipeline for a group of entities.

    Builds a Celery chain from the ProcessorRegistry's batch pipeline based on
    entity type. The first stage takes explicit entity IDs; subsequent stages
    receive the previous result containing ``needs_processing`` IDs.

    Each task in the chain is assigned a Celery priority derived from its
    processor registry priority, so that all hashing tasks across all batches
    complete before any metadata tasks, etc.

    Args:
        entity_type_str: Entity type value (e.g., "media", "chat_message").
        entity_ids: List of entity IDs to process.
        import_run_id: Optional import run ID for progress tracking.
    """
    if not entity_ids:
        return

    entity_type = EntityType(entity_type_str)
    pipeline = ProcessorRegistry.get_batch_pipeline(entity_type)

    if not pipeline:
        if entity_ids:
            logger.warning(
                f"No batch processors registered for entity type: {entity_type_str}. "
                f"{len(entity_ids)} entities will not be processed."
            )
        return

    # Build Celery chain: first task gets explicit IDs, rest chain via previous_result.
    # Each task is assigned a priority from its processor registry priority.
    first_config = pipeline[0]
    first_priority = processor_to_celery_priority(first_config.priority)
    tasks = [
        first_config.batch_task_func.s(  # type: ignore[union-attr]
            entity_type_str, entity_ids, import_run_id
        ).set(priority=first_priority)
    ]

    for config in pipeline[1:]:
        celery_priority = processor_to_celery_priority(config.priority)
        tasks.append(
            config.batch_task_func.s(entity_type_str).set(  # type: ignore[union-attr]
                priority=celery_priority
            )
        )

    chain(*tasks).apply_async()
    logger.debug(
        f"Queued batch pipeline for {len(entity_ids)} {entity_type_str} entities: "
        f"{[c.processor_class.NAME for c in pipeline]}"
    )


def run_entity_pipeline(entity_type_str: str, entity_id: str) -> None:
    """Queue processing pipeline for a single entity.

    Convenience wrapper around run_batch_entity_pipeline() for single-entity
    reprocessing (e.g., manual trigger from web UI).

    Args:
        entity_type_str: Entity type value (e.g., "media", "chat_message").
        entity_id: ID of the entity to process.
    """
    run_batch_entity_pipeline(entity_type_str, [entity_id], import_run_id=None)


# -----------------------------------------------------------------------------
# Per-Linker Celery Tasks
# -----------------------------------------------------------------------------


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="pipeline",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_temporal_linker_batch(
    self: Task[..., dict[str, Any]],
    import_run_id: str,
    entity_type_str: str,
    entity_ids: list[str],
) -> dict[str, Any]:
    """Run the temporal linker on a batch of entities of a single type.

    Includes a preemption guard: if processing tasks are still pending,
    re-queues itself with a countdown to avoid linking incomplete data.

    Args:
        import_run_id: ID of the import run (for logging).
        entity_type_str: Entity type string.
        entity_ids: List of entity ID strings.

    Returns:
        Dict with linker statistics.
    """
    if has_pending_processing(celery_app):
        logger.info("Temporal linker: processing tasks still pending, re-queueing")
        raise self.retry(countdown=_LINKER_REQUEUE_COUNTDOWN)

    return _run_linker_task(import_run_id, entity_type_str, entity_ids, linker_name="temporal")


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="pipeline",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_spatial_linker_batch(
    self: Task[..., dict[str, Any]],
    import_run_id: str,
    entity_type_str: str,
    entity_ids: list[str],
) -> dict[str, Any]:
    """Run the spatial linker on a batch of entities of a single type.

    Args:
        import_run_id: ID of the import run (for logging).
        entity_type_str: Entity type string.
        entity_ids: List of entity ID strings.

    Returns:
        Dict with linker statistics.
    """
    if has_pending_processing(celery_app):
        logger.info("Spatial linker: processing tasks still pending, re-queueing")
        raise self.retry(countdown=_LINKER_REQUEUE_COUNTDOWN)

    return _run_linker_task(import_run_id, entity_type_str, entity_ids, linker_name="spatial")


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="pipeline",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_semantic_linker_batch(
    self: Task[..., dict[str, Any]],
    import_run_id: str,
    entity_type_str: str,
    entity_ids: list[str],
) -> dict[str, Any]:
    """Run the semantic linker on a batch of entities of a single type.

    Args:
        import_run_id: ID of the import run (for logging).
        entity_type_str: Entity type string.
        entity_ids: List of entity ID strings.

    Returns:
        Dict with linker statistics.
    """
    if has_pending_processing(celery_app):
        logger.info("Semantic linker: processing tasks still pending, re-queueing")
        raise self.retry(countdown=_LINKER_REQUEUE_COUNTDOWN)

    return _run_linker_task(import_run_id, entity_type_str, entity_ids, linker_name="semantic")


def _run_linker_task(
    import_run_id: str,
    entity_type_str: str,
    entity_ids: list[str],
    *,
    linker_name: str,
) -> dict[str, Any]:
    """Shared implementation for running a single linker on one entity type.

    Args:
        import_run_id: ID of the import run.
        entity_type_str: Entity type string.
        entity_ids: List of entity ID strings.
        linker_name: Which linker to run ("temporal", "spatial", "semantic").

    Returns:
        Dict with linker statistics.
    """
    from potluck.db.session import get_engine
    from potluck.pipeline.processing.linkers import (
        SemanticLinker,
        SpatialLinker,
        TemporalLinker,
    )

    linker_map = {
        "temporal": TemporalLinker,
        "spatial": SpatialLinker,
        "semantic": SemanticLinker,
    }

    linker_cls = linker_map[linker_name]
    entity_type = EntityType(entity_type_str)
    uuids = [UUID(eid) for eid in entity_ids]

    logger.info(
        f"{linker_name} linker task for import {import_run_id}: "
        f"{len(entity_ids)} {entity_type_str} entities"
    )

    try:
        engine = get_engine()
        with Session(engine) as session:
            linker = linker_cls()
            result = linker.run(session, entity_type, uuids)
            task_result = {
                "import_run_id": import_run_id,
                "completed": len(entity_ids),
                "failed": 0,
                **result,
            }
            # Update processing progress for linker stage
            from potluck.pipeline.processing.core.base import update_processing_progress

            update_processing_progress(import_run_id, linker_name, entity_type, task_result)
            return task_result
    except Exception as e:
        logger.exception(f"{linker_name} linker failed: {e}")
        if is_transient_error(e):
            raise
        raise Reject(str(e), requeue=False) from e


# -----------------------------------------------------------------------------
# Linker Dispatch Helpers (called by orchestrator)
# -----------------------------------------------------------------------------


def dispatch_temporal_linker(
    import_run_id: str, entity_type_str: str, entity_ids: list[str]
) -> None:
    """Dispatch temporal linker task if entity type is supported."""
    from potluck.pipeline.processing.linkers.temporal import TemporalLinker

    if EntityType(entity_type_str) in TemporalLinker.SUPPORTED_ENTITY_TYPES:
        run_temporal_linker_batch.apply_async(
            args=(import_run_id, entity_type_str, entity_ids),
            priority=PRIORITY_LINK,
        )


def dispatch_spatial_linker(
    import_run_id: str, entity_type_str: str, entity_ids: list[str]
) -> None:
    """Dispatch spatial linker task if entity type is supported."""
    from potluck.pipeline.processing.linkers.spatial import SpatialLinker

    if EntityType(entity_type_str) in SpatialLinker.SUPPORTED_ENTITY_TYPES:
        run_spatial_linker_batch.apply_async(
            args=(import_run_id, entity_type_str, entity_ids),
            priority=PRIORITY_LINK,
        )


def dispatch_semantic_linker(
    import_run_id: str, entity_type_str: str, entity_ids: list[str]
) -> None:
    """Dispatch semantic linker task if entity type is supported."""
    from potluck.pipeline.processing.linkers.semantic import SemanticLinker

    if EntityType(entity_type_str) in SemanticLinker.SUPPORTED_ENTITY_TYPES:
        run_semantic_linker_batch.apply_async(
            args=(import_run_id, entity_type_str, entity_ids),
            priority=PRIORITY_LINK,
        )


# Re-export batch tasks for direct access
from potluck.pipeline.processing.processors.captioning import run_captioning_batch  # noqa: E402
from potluck.pipeline.processing.processors.clustering import cluster_unassigned_faces  # noqa: E402
from potluck.pipeline.processing.processors.embeddings import (  # noqa: E402
    run_media_embedding_batch,
    run_multimodal_text_embedding_batch,
    run_text_embedding_batch,
)
from potluck.pipeline.processing.processors.faces import run_faces_batch  # noqa: E402
from potluck.pipeline.processing.processors.hashing import run_hashing_batch  # noqa: E402
from potluck.pipeline.processing.processors.metadata import run_metadata_batch  # noqa: E402
from potluck.pipeline.processing.processors.ocr import run_ocr_batch  # noqa: E402

__all__ = [
    # Pipeline orchestration
    "run_batch_entity_pipeline",
    "run_entity_pipeline",
    # Linker dispatch helpers
    "dispatch_temporal_linker",
    "dispatch_spatial_linker",
    "dispatch_semantic_linker",
    # Linker Celery tasks
    "run_temporal_linker_batch",
    "run_spatial_linker_batch",
    "run_semantic_linker_batch",
    # Batch processor tasks (pipeline stages)
    "run_hashing_batch",
    "run_metadata_batch",
    "run_ocr_batch",
    "run_faces_batch",
    "run_captioning_batch",
    "run_text_embedding_batch",
    "run_multimodal_text_embedding_batch",
    "run_media_embedding_batch",
    # Other batch tasks
    "cluster_unassigned_faces",
]
