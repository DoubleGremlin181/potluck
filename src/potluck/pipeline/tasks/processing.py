"""Celery task orchestration for processing pipeline.

Individual processor tasks are defined in their respective modules
(e.g., processing/hashing.py). This module provides:
- Pipeline orchestration (run_processing_pipeline, run_basic_processing)
- Re-exports for convenience

Auto-discovery: Importing the processing module triggers automatic discovery
and registration of all processor tasks via pkgutil.
"""

from __future__ import annotations

from celery import chain

# Import processing module to trigger auto-discovery of all processor tasks
import potluck.pipeline.processing  # noqa: F401

# Import specific tasks for explicit pipeline construction and re-export
from potluck.pipeline.processing.captioning import run_captioning_processor
from potluck.pipeline.processing.clustering import cluster_unassigned_faces
from potluck.pipeline.processing.faces import run_faces_processor
from potluck.pipeline.processing.hashing import run_hashing_processor
from potluck.pipeline.processing.metadata import run_metadata_processor
from potluck.pipeline.processing.ocr import run_ocr_processor

__all__ = [
    "run_hashing_processor",
    "run_metadata_processor",
    "run_ocr_processor",
    "run_faces_processor",
    "run_captioning_processor",
    "cluster_unassigned_faces",
    "run_processing_pipeline",
    "run_basic_processing",
]


def run_processing_pipeline(media_id: str) -> None:
    """Trigger full processing pipeline for a media item.

    Chains processors in order: hashing -> metadata -> ocr -> faces -> caption
    """
    chain(
        run_hashing_processor.si(media_id),
        run_metadata_processor.si(media_id),
        run_ocr_processor.si(media_id),
        run_faces_processor.si(media_id),
        run_captioning_processor.si(media_id),
    ).apply_async()


def run_basic_processing(media_id: str) -> None:
    """Trigger basic processing (hashing + metadata only)."""
    chain(
        run_hashing_processor.si(media_id),
        run_metadata_processor.si(media_id),
    ).apply_async()
