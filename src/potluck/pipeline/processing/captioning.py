"""Image captioning processor using BLIP-2."""

import time
from pathlib import Path
from typing import Any, ClassVar

from celery import Task
from celery.exceptions import Retry
from PIL import Image
from sqlmodel import SQLModel

from potluck.core.celery import (
    MAX_RETRIES,
    RETRY_BACKOFF,
    RETRY_BACKOFF_MAX,
    celery_app,
)
from potluck.core.logging import get_logger
from potluck.models.base import EntityType
from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import StageResult, StageStatus
from potluck.pipeline.processing.base import BaseProcessor, run_processor_task
from potluck.pipeline.processing.ml import DEFAULT_CAPTIONING_MODEL, MLModels
from potluck.pipeline.processing.registry import ProcessorRegistry

logger = get_logger(__name__)


@ProcessorRegistry.register(priority=50)
class CaptioningProcessor(BaseProcessor):
    """Processor for generating AI image captions using BLIP-2.

    Generates human-readable alt-text descriptions for images using the
    BLIP-2 model from Salesforce. Uses MLModels for centralized model loading.
    """

    NAME: ClassVar[str] = "captioning"
    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {EntityType.MEDIA}
    PERSIST_FIELDS: ClassVar[list[str]] = ["caption"]

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_CAPTIONING_MODEL,
        max_length: int = 50,
        device: str | None = None,
    ) -> None:
        """Initialize the captioning processor.

        Args:
            model_name: HuggingFace model identifier for BLIP-2.
            max_length: Maximum length of generated captions.
            device: Device to run model on ('cuda', 'cpu', or None for auto based on GPU env var).
        """
        self._model_name = model_name
        self._max_length = max_length
        self._models = MLModels(device=device)
        self._processor: Any = None
        self._model: Any = None

    def _load_model(self) -> None:
        """Lazy load the BLIP-2 model and processor from MLModels."""
        if self._processor is not None:
            return

        self._model, self._processor = self._models.get_captioning_model(self._model_name)

    def should_execute(self, entity: SQLModel) -> bool:
        """Only process images."""
        media: Media = entity  # type: ignore[assignment]
        return media.media_type == MediaType.IMAGE

    def execute(self, entity: SQLModel) -> StageResult:
        """Generate an AI caption for the media.

        Args:
            entity: The media entity to process.

        Returns:
            StageResult with the generated caption.
        """
        media: Media = entity  # type: ignore[assignment]
        start_time = time.monotonic()

        if not self.should_execute(entity):
            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.SKIPPED,
            )

        file_path = Path(media.file_path)
        if not file_path.exists():
            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.FAILED,
                error_message=f"File not found: {media.file_path}",
            )

        try:
            self._load_model()

            image = Image.open(file_path).convert("RGB")

            inputs = self._processor(images=image, return_tensors="pt")

            if hasattr(self._model, "device"):
                inputs = inputs.to(self._model.device)

            generated_ids = self._model.generate(
                **inputs,
                max_length=self._max_length,
            )

            caption = self._processor.batch_decode(generated_ids, skip_special_tokens=True)[
                0
            ].strip()

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.COMPLETED,
                processing_time_ms=elapsed_ms,
                data={
                    "caption": caption,
                    "model_name": self._model_name,
                    "max_length": self._max_length,
                },
            )

        except Exception as e:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            logger.exception(f"Captioning failed for {media.file_path}: {e}")
            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.FAILED,
                processing_time_ms=elapsed_ms,
                error_message=f"Captioning failed: {e}",
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
def run_captioning_processor(
    self: "Task[..., dict[str, Any]]",
    entity_type: str,
    entity_id: str,
) -> dict[str, Any]:
    """Generate AI caption for an entity."""
    return run_processor_task(self, EntityType(entity_type), entity_id, CaptioningProcessor)


# Register the task with the processor
ProcessorRegistry.set_task(CaptioningProcessor.NAME, run_captioning_processor)
