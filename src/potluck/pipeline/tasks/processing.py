"""Celery task orchestration for processing pipeline.

This module provides:
- run_batch_entity_pipeline(): Queue batch-by-processor pipeline for a group of entities
- run_entity_pipeline(): Queue pipeline for a single entity (wraps batch with [id])
- run_linkers_batch(): Queue batch linkers after import completes

Processing Architecture:
    Entities are processed in batches grouped by entity type. Each batch stage loads
    ONE model, processes ALL eligible entities, then unloads. This keeps peak memory
    at the size of the largest single model (~460MB Florence-2) instead of all models
    simultaneously (~6.3GB).

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
from potluck.pipeline.processing.core.registry import ProcessorRegistry

logger = get_logger(__name__)


def run_batch_entity_pipeline(entity_type_str: str, entity_ids: list[str]) -> None:
    """Queue batch-by-processor pipeline for a group of entities.

    Builds a Celery chain from the ProcessorRegistry's batch pipeline based on
    entity type. The first stage (hashing) takes explicit IDs; subsequent stages
    receive the previous result containing ``needs_processing`` IDs.

    Each stage loads one model, processes all entities, then the task_postrun
    signal unloads it before the next stage starts.

    Args:
        entity_type_str: Entity type value (e.g., "media", "chat_message").
        entity_ids: List of entity IDs to process.
    """
    if not entity_ids:
        return

    entity_type = EntityType(entity_type_str)
    pipeline = ProcessorRegistry.get_batch_pipeline(entity_type)

    if not pipeline:
        logger.debug(f"No batch processors registered for entity type: {entity_type_str}")
        return

    # Build Celery chain: first task gets explicit IDs, rest chain via previous_result
    first_config = pipeline[0]
    tasks = [
        first_config.batch_task_func.s(entity_type_str, entity_ids)  # type: ignore[union-attr]
    ]

    for config in pipeline[1:]:
        # Subsequent tasks receive previous_result as first arg via .s()
        tasks.append(
            config.batch_task_func.s(entity_type_str)  # type: ignore[union-attr]
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
    run_batch_entity_pipeline(entity_type_str, [entity_id])


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
    "run_linkers_batch",
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
    "run_linkers_batch_task",
]
