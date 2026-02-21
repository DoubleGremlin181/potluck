"""Celery tasks for pipeline operations.

This module provides Celery tasks for running ingestion and processing
in the background, enabling progress tracking from both CLI and web UI.
"""

from potluck.pipeline.tasks.ingestion import (
    cancel_ingestion,
    run_ingestion,
    start_ingestion,
)
from potluck.pipeline.tasks.processing import (
    cluster_unassigned_faces,
    run_batch_entity_pipeline,
    run_entity_pipeline,
)

__all__ = [
    # Ingestion tasks
    "run_ingestion",
    "cancel_ingestion",
    "start_ingestion",
    # Processing pipeline
    "run_batch_entity_pipeline",
    "run_entity_pipeline",
    "cluster_unassigned_faces",
]
