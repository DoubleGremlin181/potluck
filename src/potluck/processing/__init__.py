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

__all__ = [
    # Base classes
    "BaseProcessor",
    "BatchProcessingResult",
    "ProcessingResult",
    "ProcessingStatus",
]
