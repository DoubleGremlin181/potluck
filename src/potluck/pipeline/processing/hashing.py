"""Hashing stage for media file deduplication and similarity detection.

This module provides:
- SHA256 hashing for exact file matching (deduplication)
- Perceptual hashing (pHash) for visual similarity detection
"""

import time
from pathlib import Path
from typing import ClassVar

import imagehash
from PIL import Image

from potluck.core.exceptions import ProcessingError
from potluck.core.logging import get_logger
from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import StageResult, StageStatus
from potluck.pipeline.processing.base import BaseProcessingStage
from potluck.pipeline.utils.hashing import compute_file_hash

logger = get_logger(__name__)


class HashingStage(BaseProcessingStage):
    """Stage for computing file and perceptual hashes.

    Computes:
    - SHA256 hash for all media files (exact matching)
    - Perceptual hash (pHash) for images (visual similarity)

    The pHash algorithm uses DCT (Discrete Cosine Transform) which is robust
    against minor image modifications like resizing, compression, and color
    adjustments.
    """

    NAME: ClassVar[str] = "hashing"

    def execute(self, media: Media) -> StageResult:
        """Compute hashes for a media file.

        Args:
            media: Media item to process.

        Returns:
            StageResult with file_hash and optionally perceptual_hash.
        """
        start_time = time.monotonic()

        try:
            path = Path(media.file_path)

            if not path.exists():
                return StageResult(
                    item_id=media.id,
                    stage_name=self.NAME,
                    status=StageStatus.FAILED,
                    error_message=f"File not found: {media.file_path}",
                )

            # Compute SHA256 hash for all files
            file_hash = compute_file_hash(path)

            # Compute perceptual hash for images only
            perceptual_hash: str | None = None
            if media.media_type == MediaType.IMAGE:
                perceptual_hash = self._compute_perceptual_hash(path)

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.COMPLETED,
                processing_time_ms=elapsed_ms,
                data={
                    "file_hash": file_hash,
                    "perceptual_hash": perceptual_hash,
                },
            )

        except ProcessingError as e:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.FAILED,
                error_message=str(e),
                processing_time_ms=elapsed_ms,
            )
        except Exception as e:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            logger.exception(f"Hashing failed for {media.file_path}: {e}")
            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.FAILED,
                error_message=f"Hashing failed: {e}",
                processing_time_ms=elapsed_ms,
            )

    def _compute_perceptual_hash(self, path: Path) -> str:
        """Compute perceptual hash (pHash) for an image.

        Uses DCT-based perceptual hashing which is:
        - Robust to resizing, compression, minor edits
        - Fast to compute
        - Good for near-duplicate detection

        Args:
            path: Path to the image file.

        Returns:
            Hex-encoded pHash string.

        Raises:
            ProcessingError: If the image cannot be opened or processed.
        """
        try:
            with Image.open(path) as img:
                phash = imagehash.phash(img)
                return str(phash)
        except Exception as e:
            raise ProcessingError(f"Failed to compute perceptual hash: {e}") from e


def compute_phash_distance(hash1: str, hash2: str) -> int:
    """Compute Hamming distance between two perceptual hashes.

    Lower distance means more similar images:
    - 0: Identical or near-identical
    - 1-10: Very similar (likely same image with minor edits)
    - 11-20: Somewhat similar
    - >20: Different images

    Args:
        hash1: First perceptual hash string.
        hash2: Second perceptual hash string.

    Returns:
        Hamming distance between the hashes.
    """
    h1 = imagehash.hex_to_hash(hash1)
    h2 = imagehash.hex_to_hash(hash2)
    return h1 - h2
