"""Celery task orchestration for processing pipeline.

This module provides:
- run_entity_pipeline(): Queue appropriate processors for any entity type
- run_linkers_batch(): Queue batch linkers after import completes
- Legacy functions for backward compatibility

Auto-discovery: Importing the processing module triggers automatic discovery
and registration of all processor tasks via pkgutil.
"""

from __future__ import annotations

from typing import Any

from celery import Task, chain
from celery.exceptions import Retry

# Import processing module to trigger auto-discovery of all processor tasks
import potluck.pipeline.processing  # noqa: F401
from potluck.core.celery import (
    MAX_RETRIES,
    RETRY_BACKOFF,
    RETRY_BACKOFF_MAX,
    celery_app,
)
from potluck.core.logging import get_logger
from potluck.models.base import EntityType

# Import registry for dynamic pipeline construction
from potluck.pipeline.processing.registry import ProcessorRegistry

logger = get_logger(__name__)


def run_entity_pipeline(entity_type_str: str, entity_id: str) -> None:
    """Queue appropriate processors for any entity type.

    Builds a Celery chain from the ProcessorRegistry based on entity type.
    Processors run in priority order (lower priority values first).

    Args:
        entity_type_str: Entity type value (e.g., "media", "chat_message").
        entity_id: ID of the entity to process.
    """
    entity_type = EntityType(entity_type_str)

    # Get pipeline for this entity type from registry
    pipeline = ProcessorRegistry.get_pipeline(entity_type)

    if not pipeline:
        logger.debug(f"No processors registered for entity type: {entity_type_str}")
        return

    # Build Celery chain from pipeline
    # Note: task_func is a Celery task with .si() method
    tasks = [
        config.task_func.si(entity_type_str, entity_id)  # type: ignore[attr-defined]
        for config in pipeline
    ]

    if tasks:
        chain(*tasks).apply_async()
        logger.debug(
            f"Queued processing pipeline for {entity_type_str} {entity_id}: "
            f"{[c.processor_class.NAME for c in pipeline]}"
        )


# Legacy function for backward compatibility
def run_processing_pipeline(media_id: str) -> None:
    """Trigger full processing pipeline for a media item (legacy).

    This function maintains backward compatibility. New code should use
    run_entity_pipeline() instead.

    Args:
        media_id: ID of the media item to process.
    """
    run_entity_pipeline(EntityType.MEDIA.value, media_id)


def run_basic_processing(media_id: str) -> None:
    """Trigger basic processing (hashing + metadata only).

    Args:
        media_id: ID of the media item to process.
    """
    from potluck.pipeline.processing.hashing import run_hashing_processor
    from potluck.pipeline.processing.metadata import run_metadata_processor

    chain(
        run_hashing_processor.si(EntityType.MEDIA.value, media_id),
        run_metadata_processor.si(EntityType.MEDIA.value, media_id),
    ).apply_async()


# -----------------------------------------------------------------------------
# Batch Linker Task
# -----------------------------------------------------------------------------


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_linkers_batch_task(
    self: Task[..., dict[str, Any]],
    import_run_id: str,
    entity_ids_by_type: dict[str, list[str]],
) -> dict[str, Any]:
    """Run all linkers on entities from an import.

    This task runs after ingestion completes to create EntityLink records
    between related entities (temporal, spatial, semantic).

    Args:
        import_run_id: ID of the import run.
        entity_ids_by_type: Dict mapping entity type strings to lists of entity IDs.

    Returns:
        Dict with linker statistics.
    """
    from uuid import UUID

    from sqlmodel import Session

    from potluck.db.session import get_engine
    from potluck.pipeline.processing.linkers import (
        SemanticLinker,
        SpatialLinker,
        TemporalLinker,
    )

    total_entities = sum(len(ids) for ids in entity_ids_by_type.values())
    logger.info(
        f"Linker task for import {import_run_id}: "
        f"{total_entities} entities across {len(entity_ids_by_type)} types"
    )

    # Convert string IDs to UUIDs and entity type strings to EntityType enums
    entity_ids_converted: dict[EntityType, list[UUID]] = {}
    for type_str, id_strings in entity_ids_by_type.items():
        entity_type = EntityType(type_str)
        entity_ids_converted[entity_type] = [UUID(id_str) for id_str in id_strings]

    # Initialize linkers
    linkers = [
        TemporalLinker(),
        SpatialLinker(),
        SemanticLinker(),
    ]

    # Run each linker
    linker_results: list[dict[str, Any]] = []
    total_links = 0

    engine = get_engine()
    with Session(engine) as session:
        for linker in linkers:
            try:
                result = linker.run(session, entity_ids_converted)
                linker_results.append(result)
                total_links += result.get("links_persisted", 0)
            except Exception as e:
                logger.exception(f"Linker {linker.NAME} failed: {e}")
                linker_results.append(
                    {
                        "linker_name": linker.NAME,
                        "error": str(e),
                    }
                )

    return {
        "import_run_id": import_run_id,
        "entity_types": list(entity_ids_by_type.keys()),
        "total_entities": total_entities,
        "links_created": total_links,
        "linker_results": linker_results,
    }


def run_linkers_batch(import_run_id: str, entity_ids_by_type: dict[str, list[str]]) -> None:
    """Queue batch linkers for entities from an import.

    Args:
        import_run_id: ID of the import run.
        entity_ids_by_type: Dict mapping entity type strings to lists of entity IDs.
    """
    run_linkers_batch_task.delay(import_run_id, entity_ids_by_type)


# Re-export individual tasks for direct access
from potluck.pipeline.processing.captioning import run_captioning_processor  # noqa: E402
from potluck.pipeline.processing.clustering import cluster_unassigned_faces  # noqa: E402
from potluck.pipeline.processing.embeddings import (  # noqa: E402
    run_media_embedding_processor,
    run_text_embedding_processor,
)
from potluck.pipeline.processing.faces import run_faces_processor  # noqa: E402
from potluck.pipeline.processing.hashing import run_hashing_processor  # noqa: E402
from potluck.pipeline.processing.metadata import run_metadata_processor  # noqa: E402
from potluck.pipeline.processing.ocr import run_ocr_processor  # noqa: E402

__all__ = [
    # Pipeline orchestration
    "run_entity_pipeline",
    "run_linkers_batch",
    # Legacy functions
    "run_processing_pipeline",
    "run_basic_processing",
    # Individual processor tasks
    "run_hashing_processor",
    "run_metadata_processor",
    "run_ocr_processor",
    "run_faces_processor",
    "run_captioning_processor",
    "run_text_embedding_processor",
    "run_media_embedding_processor",
    # Batch tasks
    "cluster_unassigned_faces",
    "run_linkers_batch_task",
]
