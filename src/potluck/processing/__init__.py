"""Processing module for Potluck.

This module provides processors for extracting information from entities.
Current implementations focus on Media entities:
- Hashing: SHA256 + perceptual hashing for deduplication
- Metadata: EXIF extraction for GPS, timestamps, camera info
- OCR: Text extraction from images (requires ML dependencies)
- Faces: Face detection and clustering (requires ML dependencies)
- Captioning: AI-generated image descriptions (requires ML dependencies)

The BaseProcessor pattern can be extended for processing other entity types.

ML-dependent processors (OCR, Faces, Captioning) are not exported here
to avoid import errors when ML dependencies are not installed.
Import them directly from their submodules when needed:
    from potluck.processing.ocr import OCRProcessor
    from potluck.processing.faces import FaceProcessor
    from potluck.processing.captioning import CaptioningProcessor
"""

from potluck.processing.base import (
    BaseProcessor,
    BatchProcessingResult,
    ProcessingResult,
    ProcessingStatus,
)
from potluck.processing.hashing import HashingProcessor, compute_phash_distance
from potluck.processing.metadata import MetadataProcessor

__all__ = [
    # Base classes
    "BaseProcessor",
    "BatchProcessingResult",
    "ProcessingResult",
    "ProcessingStatus",
    # Processors (non-ML)
    "HashingProcessor",
    "MetadataProcessor",
    # Utilities
    "compute_phash_distance",
]
