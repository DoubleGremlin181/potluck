"""OCR text extraction processor for images.

This module provides OCR (Optical Character Recognition) capabilities using EasyOCR
to extract text from images. Supports multiple languages with auto-detection.
"""

import time
from pathlib import Path
from typing import Any, ClassVar

from celery import Task
from celery.exceptions import Retry
from sqlmodel import SQLModel

from potluck.core.celery import (
    MAX_RETRIES,
    RETRY_BACKOFF,
    RETRY_BACKOFF_MAX,
    celery_app,
)
from potluck.core.exceptions import ProcessingError
from potluck.core.logging import get_logger
from potluck.models.base import EntityType
from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import BatchStageResult, StageResult, StageStatus
from potluck.pipeline.processing.core.base import BaseProcessor, run_processor_task
from potluck.pipeline.processing.core.ml import MLModels
from potluck.pipeline.processing.core.registry import ProcessorRegistry

logger = get_logger(__name__)


# Default languages for OCR (English)
DEFAULT_LANGUAGES = ["en"]


@ProcessorRegistry.register(priority=30)
class OCRProcessor(BaseProcessor):
    """Processor for extracting text from images using EasyOCR.

    EasyOCR supports 80+ languages and can detect text in:
    - Natural scene images (signs, labels, etc.)
    - Document images (scanned documents, screenshots)
    - Handwritten text (limited support)

    Uses MLModels for centralized model loading and GPU configuration.
    """

    NAME: ClassVar[str] = "ocr"
    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {EntityType.MEDIA}
    PERSIST_FIELDS: ClassVar[list[str]] = ["ocr_text"]

    def __init__(
        self,
        languages: list[str] | None = None,
        device: str | None = None,
    ) -> None:
        """Initialize the OCR processor.

        Args:
            languages: List of language codes to recognize (e.g., ['en', 'es']).
                       Defaults to English only.
            device: Device to use ('cuda', 'cpu', or None for auto based on GPU env var).
        """
        self._languages = languages or DEFAULT_LANGUAGES
        self._models = MLModels(device=device)

    @property
    def reader(self) -> Any:
        """Get the OCR reader from MLModels."""
        return self._models.get_ocr_reader(self._languages)

    def should_execute(self, entity: SQLModel) -> bool:
        """Only process images which may contain text."""
        media: Media = entity  # type: ignore[assignment]
        return media.media_type == MediaType.IMAGE

    def execute(self, entity: SQLModel) -> StageResult:
        """Extract text from an image using OCR.

        Args:
            entity: Media entity to process.

        Returns:
            StageResult with extracted text.
        """
        media: Media = entity  # type: ignore[assignment]
        start_time = time.monotonic()

        if not self.should_execute(entity):
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

    def execute_batch(self, entities: list[SQLModel]) -> BatchStageResult:
        """Process a batch of images."""
        media_items: list[Media] = entities  # type: ignore[assignment]
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


# -----------------------------------------------------------------------------
# Celery Task
# -----------------------------------------------------------------------------


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_ocr_processor(
    self: "Task[..., dict[str, Any]]",
    entity_type: str,
    entity_id: str,
) -> dict[str, Any]:
    """Run OCR on an entity."""
    return run_processor_task(self, EntityType(entity_type), entity_id, OCRProcessor)


# Register the task with the processor
ProcessorRegistry.set_task(OCRProcessor.NAME, run_ocr_processor)
