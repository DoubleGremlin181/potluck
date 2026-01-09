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
    run_basic_processing,
    run_captioning_processor,
    run_faces_processor,
    run_hashing_processor,
    run_metadata_processor,
    run_ocr_processor,
    run_processing_pipeline,
)

__all__ = [
    # Ingestion tasks
    "run_ingestion",
    "cancel_ingestion",
    "start_ingestion",
    # Processing tasks
    "run_hashing_processor",
    "run_metadata_processor",
    "run_ocr_processor",
    "run_faces_processor",
    "run_captioning_processor",
    "run_processing_pipeline",
    "run_basic_processing",
    "cluster_unassigned_faces",
]
