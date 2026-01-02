"""Unified pipeline module for ingestion and processing.

This module provides a unified interface for:
- Ingesting data from various sources (Google Takeout, Reddit exports, etc.)
- Processing media files (hashing, metadata extraction, OCR, face detection, captioning)

Public API
----------
Core Classes:
    Stage: Abstract base class for all pipeline stages
    BaseIngestionStage: Base class for ingestion stages
    BaseProcessingStage: Base class for processing stages
    PipelineOrchestrator: Main orchestrator for running ingestion pipelines

DTOs:
    StageStatus: Enum for stage execution status
    StageResult: Result from a single stage execution
    BatchStageResult: Result from batch stage execution
    PipelineFilter: Filters for pipeline execution
    PipelineStats: Statistics from pipeline execution
    PipelineResult: Complete result from pipeline execution
    DetectionResult: Result from stage detection
    DiscoveryResult: Result from file discovery

Orchestration:
    discover: Discover files without ingesting
    ingest: Convenience function for one-off ingestion

Tasks:
    run_ingestion: Celery task for async ingestion
    cancel_ingestion: Celery task to cancel running ingestion
    start_ingestion: Start ingestion job (sync wrapper)
    run_processing_pipeline: Run full processing pipeline
    run_basic_processing: Run hashing + metadata only

Registry:
    register: Decorator to register ingestion stages
    detect_stage: Detect appropriate stage for a file
    get_stage: Get a registered stage by name
    list_stages: List all registered stages

Processing Stages (non-ML):
    HashingStage: Compute file hashes
    MetadataStage: Extract EXIF metadata

ML-Dependent Stages:
    These require `pip install potluck[ml]` and must be imported directly:
    - potluck.pipeline.processing.ocr.OCRStage
    - potluck.pipeline.processing.faces.FaceStage
    - potluck.pipeline.processing.captioning.CaptioningStage

Example Usage
-------------
Basic ingestion:

    from potluck.pipeline import ingest, PipelineFilter

    result = ingest(
        source_path="/path/to/takeout.zip",
        filter=PipelineFilter(start_date=datetime(2023, 1, 1)),
    )
    print(f"Ingested {result.stats.entities_created} entities")

Async ingestion via Celery:

    from potluck.pipeline import start_ingestion

    job_id = start_ingestion("/path/to/data")

Custom processing:

    from potluck.pipeline import HashingStage, MetadataStage

    hashing = HashingStage()
    result = hashing.execute(media)
"""

# Core base classes
from potluck.pipeline.base import Stage

# DTOs
from potluck.pipeline.dtos import (
    BatchStageResult,
    DetectionResult,
    DiscoveryResult,
    PipelineFilter,
    PipelineResult,
    PipelineStats,
    StageResult,
    StageStatus,
)

# Ingestion
from potluck.pipeline.ingestion import (
    BaseIngestionStage,
    clear_registry,
    detect_stage,
    get_stage,
    list_stages,
    register,
)

# Orchestration
from potluck.pipeline.orchestrator import (
    PipelineOrchestrator,
    discover,
    ingest,
)

# Processing base
from potluck.pipeline.processing import BaseProcessingStage

# Non-ML processing stages (safe to import without ML deps)
from potluck.pipeline.processing.hashing import HashingStage
from potluck.pipeline.processing.metadata import MetadataStage

# Tasks
from potluck.pipeline.tasks.ingestion import (
    cancel_ingestion,
    run_ingestion,
    start_ingestion,
)
from potluck.pipeline.tasks.processing import (
    run_basic_processing,
    run_processing_pipeline,
)

__all__ = [
    # Base classes
    "Stage",
    "BaseIngestionStage",
    "BaseProcessingStage",
    # DTOs
    "StageStatus",
    "StageResult",
    "BatchStageResult",
    "PipelineFilter",
    "PipelineStats",
    "PipelineResult",
    "DetectionResult",
    "DiscoveryResult",
    # Orchestration
    "PipelineOrchestrator",
    "discover",
    "ingest",
    # Registry
    "register",
    "detect_stage",
    "get_stage",
    "list_stages",
    "clear_registry",
    # Processing stages (non-ML)
    "HashingStage",
    "MetadataStage",
    # Tasks
    "run_ingestion",
    "cancel_ingestion",
    "start_ingestion",
    "run_processing_pipeline",
    "run_basic_processing",
]
