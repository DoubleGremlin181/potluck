"""Image captioning processor using BLIP-2."""

import time
from pathlib import Path
from typing import Any, ClassVar

import torch
from celery import Task
from celery.exceptions import Retry
from PIL import Image
from transformers import Blip2ForConditionalGeneration, Blip2Processor

from potluck.core.celery import (
    MAX_RETRIES,
    RETRY_BACKOFF,
    RETRY_BACKOFF_MAX,
    celery_app,
)
from potluck.core.logging import get_logger
from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import StageResult, StageStatus
from potluck.pipeline.processing.base import BaseProcessor, run_processor_task

logger = get_logger(__name__)


class CaptioningProcessor(BaseProcessor):
    """Processor for generating AI image captions using BLIP-2.

    Generates human-readable alt-text descriptions for images using the
    BLIP-2 model from Salesforce.
    """

    NAME: ClassVar[str] = "captioning"
    PERSIST_FIELDS: ClassVar[list[str]] = ["caption"]

    DEFAULT_MODEL = "Salesforce/blip2-opt-2.7b"

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_MODEL,
        max_length: int = 50,
        device: str | None = None,
    ) -> None:
        """Initialize the captioning processor.

        Args:
            model_name: HuggingFace model identifier for BLIP-2.
            max_length: Maximum length of generated captions.
            device: Device to run model on ('cuda', 'cpu', or None for auto).
        """
        self._model_name = model_name
        self._max_length = max_length
        self._device = device
        self._processor: Any = None
        self._model: Any = None

    def _load_model(self) -> None:
        """Lazy load the BLIP-2 model and processor."""
        if self._processor is not None:
            return

        if self._device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            device = self._device

        self._processor = Blip2Processor.from_pretrained(self._model_name)

        if device == "cuda":
            self._model = Blip2ForConditionalGeneration.from_pretrained(
                self._model_name,
                torch_dtype=torch.float16,
                device_map="auto",
            )
        else:
            self._model = Blip2ForConditionalGeneration.from_pretrained(
                self._model_name,
            )
            self._model.to(device)

    def should_execute(self, media: Media) -> bool:
        """Only process images."""
        return media.media_type == MediaType.IMAGE

    def execute(self, media: Media) -> StageResult:
        """Generate an AI caption for the media.

        Args:
            media: The media item to process.

        Returns:
            StageResult with the generated caption.
        """
        start_time = time.monotonic()

        if not self.should_execute(media):
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
def run_captioning_processor(self: "Task[..., dict[str, Any]]", media_id: str) -> dict[str, Any]:
    """Generate AI caption for a media item."""
    return run_processor_task(self, media_id, CaptioningProcessor)
