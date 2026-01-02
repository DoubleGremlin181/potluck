"""OCR text extraction stage for images.

This module provides OCR (Optical Character Recognition) capabilities using EasyOCR
to extract text from images. Supports multiple languages with auto-detection.

Requires ML dependencies: pip install potluck[ml]
"""

import time
from pathlib import Path
from typing import Any, ClassVar

import easyocr

from potluck.core.exceptions import ProcessingError
from potluck.core.logging import get_logger
from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import BatchStageResult, StageResult, StageStatus
from potluck.pipeline.processing.base import BaseProcessingStage

logger = get_logger(__name__)


# Default languages for OCR (English)
DEFAULT_LANGUAGES = ["en"]


class OCRStage(BaseProcessingStage):
    """Stage for extracting text from images using EasyOCR.

    EasyOCR supports 80+ languages and can detect text in:
    - Natural scene images (signs, labels, etc.)
    - Document images (scanned documents, screenshots)
    - Handwritten text (limited support)

    The stage uses lazy initialization to avoid loading the heavy
    model until it's actually needed.
    """

    NAME: ClassVar[str] = "ocr"

    def __init__(
        self,
        languages: list[str] | None = None,
        gpu: bool = True,
    ) -> None:
        """Initialize the OCR stage.

        Args:
            languages: List of language codes to recognize (e.g., ['en', 'es']).
                       Defaults to English only.
            gpu: Whether to use GPU acceleration if available. Defaults to True.
        """
        self._languages = languages or DEFAULT_LANGUAGES
        self._gpu = gpu
        self._reader: Any = None  # Lazy initialization

    @property
    def reader(self) -> Any:
        """Get the EasyOCR reader, initializing if necessary."""
        if self._reader is None:
            try:
                logger.info(
                    f"Initializing EasyOCR with languages: {self._languages}, GPU: {self._gpu}"
                )
                self._reader = easyocr.Reader(self._languages, gpu=self._gpu)
            except Exception as e:
                raise ProcessingError(f"Failed to initialize EasyOCR: {e}") from e

        return self._reader

    def should_execute(self, media: Media) -> bool:
        """Only process images which may contain text."""
        return media.media_type == MediaType.IMAGE

    def execute(self, media: Media) -> StageResult:
        """Extract text from an image using OCR.

        Args:
            media: Media item to process.

        Returns:
            StageResult with extracted text.
        """
        start_time = time.monotonic()

        if not self.should_execute(media):
            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.SKIPPED,
                error_message="Not an image file",
            )

        try:
            path = Path(media.file_path)

            if not path.exists():
                return StageResult(
                    item_id=media.id,
                    stage_name=self.NAME,
                    status=StageStatus.FAILED,
                    error_message=f"File not found: {media.file_path}",
                )

            # Run OCR
            results = self.reader.readtext(str(path))

            # Extract text from results (each is a tuple: bbox, text, confidence)
            text_parts = [r[1] for r in results if len(r) > 1]
            ocr_text = " ".join(text_parts).strip()

            # Calculate average confidence if results exist
            avg_confidence = 0.0
            if results:
                confidences = [r[2] for r in results if len(r) > 2]
                if confidences:
                    avg_confidence = sum(confidences) / len(confidences)

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.COMPLETED,
                processing_time_ms=elapsed_ms,
                data={
                    "ocr_text": ocr_text,
                    "text_regions": len(results),
                    "avg_confidence": avg_confidence,
                },
            )

        except ProcessingError:
            raise
        except Exception as e:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            logger.exception(f"OCR failed for {media.file_path}: {e}")
            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.FAILED,
                error_message=f"OCR failed: {e}",
                processing_time_ms=elapsed_ms,
            )

    def execute_batch(self, media_items: list[Media]) -> BatchStageResult:
        """Process a batch of images."""
        images = [m for m in media_items if self.should_execute(m)]
        skipped = [m for m in media_items if not self.should_execute(m)]

        results: list[StageResult] = []

        for media in skipped:
            results.append(
                StageResult(
                    item_id=media.id,
                    stage_name=self.NAME,
                    status=StageStatus.SKIPPED,
                    error_message="Not an image file",
                )
            )

        for media in images:
            results.append(self.execute(media))

        return BatchStageResult(
            stage_name=self.NAME,
            total=len(media_items),
            completed=sum(1 for r in results if r.status == StageStatus.COMPLETED),
            failed=sum(1 for r in results if r.status == StageStatus.FAILED),
            skipped=sum(1 for r in results if r.status == StageStatus.SKIPPED),
            results=results,
        )
