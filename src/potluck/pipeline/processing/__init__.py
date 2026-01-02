"""Processing stages for extracting information from media entities.

This module provides processing stages for:
- Hashing (SHA256 + perceptual hash for deduplication)
- Metadata extraction (EXIF data, GPS, timestamps)
- OCR (text extraction from images) - ML dependent
- Face detection and clustering - ML dependent
- Image captioning - ML dependent

Public API:
    - BaseProcessingStage: Abstract base class for stages
    - HashingStage: File and perceptual hashing
    - MetadataStage: EXIF metadata extraction
    - compute_phash_distance: Helper for comparing perceptual hashes

ML-dependent stages (require 'pip install potluck[ml]'):
    - OCRStage: Text extraction using EasyOCR
    - FaceStage: Face detection using DeepFace
    - CaptioningStage: Image captioning using BLIP-2
"""

from potluck.pipeline.dtos import BatchStageResult, StageResult, StageStatus
from potluck.pipeline.processing.base import BaseProcessingStage
from potluck.pipeline.processing.hashing import HashingStage, compute_phash_distance
from potluck.pipeline.processing.metadata import MetadataStage

# ML-dependent stages are not exported by default to avoid import errors
# Import directly: from potluck.pipeline.processing.ocr import OCRStage

__all__ = [
    # Base class
    "BaseProcessingStage",
    # DTOs
    "StageResult",
    "StageStatus",
    "BatchStageResult",
    # Non-ML stages
    "HashingStage",
    "MetadataStage",
    # Utilities
    "compute_phash_distance",
]
