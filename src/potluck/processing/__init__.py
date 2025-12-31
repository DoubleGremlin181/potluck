"""Media processing module for Potluck.

This module provides processors for extracting information from media files:
- Hashing: SHA256 + perceptual hashing for deduplication
- Metadata: EXIF extraction for GPS, timestamps, camera info
- OCR: Text extraction from images (Phase 4B)
- Faces: Face detection and clustering (Phase 4C)
- Captioning: AI-generated image descriptions (Phase 4D)
"""

from potluck.processing.base import (
    BaseProcessor,
    BatchProcessingResult,
    ProcessingResult,
    ProcessingStatus,
)
from potluck.processing.captioning import CaptioningProcessor
from potluck.processing.faces import FaceProcessor
from potluck.processing.hashing import HashingProcessor, compute_phash_distance
from potluck.processing.metadata import MetadataProcessor
from potluck.processing.ocr import OCRProcessor
from potluck.processing.tasks import (
    cluster_unassigned_faces,
    process_media_basic,
    process_media_caption,
    process_media_faces,
    process_media_hashing,
    process_media_metadata,
    process_media_ocr,
    process_media_pipeline,
)

__all__ = [
    # Base classes
    "BaseProcessor",
    "BatchProcessingResult",
    "ProcessingResult",
    "ProcessingStatus",
    # Processors
    "CaptioningProcessor",
    "FaceProcessor",
    "HashingProcessor",
    "MetadataProcessor",
    "OCRProcessor",
    # Utilities
    "compute_phash_distance",
    # Tasks
    "cluster_unassigned_faces",
    "process_media_basic",
    "process_media_caption",
    "process_media_faces",
    "process_media_hashing",
    "process_media_metadata",
    "process_media_ocr",
    "process_media_pipeline",
]
