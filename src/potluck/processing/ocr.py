"""OCR text extraction processor for images.

This module provides OCR (Optical Character Recognition) capabilities using EasyOCR
to extract text from images. Supports multiple languages with auto-detection.
"""

import time
from pathlib import Path
from typing import Any

import easyocr

from potluck.core.exceptions import ProcessingError
from potluck.core.logging import get_logger
from potluck.models.media import Media, MediaType
from potluck.processing.base import (
    BaseProcessor,
    BatchProcessingResult,
    ProcessingResult,
    ProcessingStatus,
)

logger = get_logger(__name__)


# Default languages for OCR (English)
DEFAULT_LANGUAGES = ["en"]


class OCRProcessor(BaseProcessor):
    """Processor for extracting text from images using EasyOCR.

    EasyOCR supports 80+ languages and can detect text in:
    - Natural scene images (signs, labels, etc.)
    - Document images (scanned documents, screenshots)
    - Handwritten text (limited support)

    The processor uses lazy initialization to avoid loading the heavy
    model until it's actually needed.
    """

    NAME = "ocr"

    def __init__(
        self,
        languages: list[str] | None = None,
        gpu: bool = True,
    ) -> None:
        """Initialize the OCR processor.

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
        """Get the EasyOCR reader, initializing if necessary.

        Returns:
            EasyOCR Reader instance.

        Raises:
            ProcessingError: If EasyOCR cannot be initialized.
        """
        if self._reader is None:
            try:
                logger.info(
                    f"Initializing EasyOCR with languages: {self._languages}, GPU: {self._gpu}"
                )
                self._reader = easyocr.Reader(self._languages, gpu=self._gpu)
            except Exception as e:
                raise ProcessingError(f"Failed to initialize EasyOCR: {e}") from e

        return self._reader

    def should_process(self, media: Media) -> bool:
        """Only process images which may contain text."""
        return media.media_type == MediaType.IMAGE

    def process(self, media: Media) -> ProcessingResult:
        """Extract text from an image using OCR.

        Args:
            media: Media item to process.

        Returns:
            ProcessingResult with extracted text.
        """
        start_time = time.monotonic()

        if not self.should_process(media):
            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.SKIPPED,
                error_message="Not an image file",
            )

        try:
            path = Path(media.file_path)

            if not path.exists():
                return ProcessingResult(
                    media_id=media.id,
                    processor_name=self.NAME,
                    status=ProcessingStatus.FAILED,
                    error_message=f"File not found: {media.file_path}",
                )

            # Run OCR
            results = self.reader.readtext(str(path))

            # Extract text from results
            # Each result is a tuple: (bbox, text, confidence)
            text_parts = [r[1] for r in results if len(r) > 1]
            ocr_text = " ".join(text_parts).strip()

            # Calculate average confidence if results exist
            avg_confidence = 0.0
            if results:
                confidences = [r[2] for r in results if len(r) > 2]
                if confidences:
                    avg_confidence = sum(confidences) / len(confidences)

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.COMPLETED,
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
            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.FAILED,
                error_message=f"OCR failed: {e}",
                processing_time_ms=elapsed_ms,
            )

    def process_batch(self, media_items: list[Media]) -> BatchProcessingResult:
        """Process a batch of images.

        EasyOCR supports batch processing for better GPU utilization.
        This method filters to images and processes them together.

        Args:
            media_items: List of media items to process.

        Returns:
            BatchProcessingResult with individual results.
        """
        # Filter to images only
        images = [m for m in media_items if self.should_process(m)]
        skipped = [m for m in media_items if not self.should_process(m)]

        results: list[ProcessingResult] = []

        # Add skipped results
        for media in skipped:
            results.append(
                ProcessingResult(
                    media_id=media.id,
                    processor_name=self.NAME,
                    status=ProcessingStatus.SKIPPED,
                    error_message="Not an image file",
                )
            )

        # Process images (batch mode if available)
        # Note: EasyOCR's batch processing requires images to be the same size
        # For simplicity, we process individually but could optimize with batching
        for media in images:
            results.append(self.process(media))

        return BatchProcessingResult(
            processor_name=self.NAME,
            total=len(media_items),
            completed=sum(1 for r in results if r.status == ProcessingStatus.COMPLETED),
            failed=sum(1 for r in results if r.status == ProcessingStatus.FAILED),
            skipped=sum(1 for r in results if r.status == ProcessingStatus.SKIPPED),
            results=results,
        )
