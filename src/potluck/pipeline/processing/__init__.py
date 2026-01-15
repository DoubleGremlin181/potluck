"""Processing module - auto-discovers and registers all processor tasks.

This module provides processors for extracting information from entities:
- Hashing (SHA256 + perceptual hash for deduplication)
- Metadata extraction (EXIF data, GPS, timestamps)
- OCR (text extraction from images)
- Face detection and clustering
- Image captioning
- Text embeddings (semantic embeddings for text entities)
- Media embeddings (CLIP, OCR text, caption embeddings)

Auto-Discovery:
    Importing this module automatically discovers and imports all processor
    modules in the processors/ subdirectory, which triggers Celery task
    registration. This means adding a new processor file will automatically
    make it available to Celery without modifying this file.

Public API:
    - BaseProcessor: Abstract base class for processors
    - run_processor_task: Shared task execution with error handling
    - HashingProcessor: File and perceptual hashing
    - MetadataProcessor: EXIF metadata extraction
    - OCRProcessor: Text extraction using EasyOCR
    - FaceProcessor: Face detection using MTCNN + ArcFace
    - CaptioningProcessor: Image captioning using BLIP-2
    - TextEmbeddingProcessor: Text embedding for text-to-text semantic search
    - MultimodalTextEmbeddingProcessor: Text embedding for cross-modal search
    - MediaEmbeddingProcessor: Visual/text embeddings for media
    - compute_phash_distance: Helper for comparing perceptual hashes
"""

import importlib
import pkgutil
from pathlib import Path

# Auto-discover all processor modules in the processors/ subdirectory and import them.
# This triggers Celery task registration for any tasks defined in those modules.
_processors_dir = Path(__file__).parent / "processors"

for _module_info in pkgutil.iter_modules([str(_processors_dir)]):
    if not _module_info.name.startswith("_"):
        importlib.import_module(f".processors.{_module_info.name}", __package__)

# Export base class and utilities from core/
# Note: These imports must come after auto-discovery to avoid circular imports
from potluck.pipeline.dtos import BatchStageResult, StageResult, StageStatus  # noqa: E402
from potluck.pipeline.processing.core.base import (  # noqa: E402
    BaseProcessor,
    run_batch_processor_task,
    run_processor_task,
)
from potluck.pipeline.processing.core.registry import (  # noqa: E402
    ProcessorConfig,
    ProcessorRegistry,
)
from potluck.pipeline.processing.processors.captioning import CaptioningProcessor  # noqa: E402
from potluck.pipeline.processing.processors.embeddings import (  # noqa: E402
    MediaEmbeddingProcessor,
    MultimodalTextEmbeddingProcessor,
    TextEmbeddingProcessor,
)
from potluck.pipeline.processing.processors.faces import FaceProcessor  # noqa: E402
from potluck.pipeline.processing.processors.hashing import (  # noqa: E402
    HashingProcessor,
    compute_phash_distance,
)
from potluck.pipeline.processing.processors.metadata import MetadataProcessor  # noqa: E402
from potluck.pipeline.processing.processors.ocr import OCRProcessor  # noqa: E402

__all__ = [
    # Base class and registry
    "BaseProcessor",
    "run_processor_task",
    "run_batch_processor_task",
    "ProcessorRegistry",
    "ProcessorConfig",
    # DTOs
    "StageResult",
    "StageStatus",
    "BatchStageResult",
    # Processors
    "HashingProcessor",
    "MetadataProcessor",
    "OCRProcessor",
    "FaceProcessor",
    "CaptioningProcessor",
    "TextEmbeddingProcessor",
    "MultimodalTextEmbeddingProcessor",
    "MediaEmbeddingProcessor",
    # Utilities
    "compute_phash_distance",
]
