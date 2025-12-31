"""Image captioning processor using BLIP-2."""

from pathlib import Path
from typing import Any

from potluck.core.exceptions import ProcessingError
from potluck.models.media import Media, MediaType
from potluck.processing.base import BaseProcessor, ProcessingResult, ProcessingStatus


class CaptioningProcessor(BaseProcessor):
    """Processor for generating AI image captions using BLIP-2.

    Generates human-readable alt-text descriptions for images using the
    BLIP-2 model from Salesforce.
    """

    NAME = "captioning"

    # Default model for captioning
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

        try:
            import torch
            from transformers import Blip2ForConditionalGeneration, Blip2Processor
        except ImportError as e:
            raise ProcessingError(
                "transformers is not installed. Install with: pip install 'potluck[ml]'"
            ) from e

        # Determine device
        if self._device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            device = self._device

        # Load processor (handles tokenization and image preprocessing)
        self._processor = Blip2Processor.from_pretrained(self._model_name)

        # Load model with appropriate dtype for the device
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

    def should_process(self, media: Media) -> bool:
        """Check if this media should be processed for captioning.

        Only processes images.

        Args:
            media: The media item to check.

        Returns:
            True if the media is an image.
        """
        return media.media_type == MediaType.IMAGE

    def process(self, media: Media) -> ProcessingResult:
        """Generate an AI caption for the media.

        Args:
            media: The media item to process.

        Returns:
            ProcessingResult with the generated caption.
        """
        import time

        start_time = time.monotonic()

        if not self.should_process(media):
            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.SKIPPED,
            )

        file_path = Path(media.file_path)
        if not file_path.exists():
            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.FAILED,
                error_message=f"File not found: {media.file_path}",
            )

        try:
            from PIL import Image
        except ImportError as e:
            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.FAILED,
                error_message=f"PIL is not installed: {e}",
            )

        try:
            # Load model on first use
            self._load_model()

            # Open and prepare image
            image = Image.open(file_path).convert("RGB")

            # Process image and generate caption
            inputs = self._processor(images=image, return_tensors="pt")

            # Move inputs to same device as model
            if hasattr(self._model, "device"):
                inputs = inputs.to(self._model.device)

            # Generate caption
            generated_ids = self._model.generate(
                **inputs,
                max_length=self._max_length,
            )

            # Decode the generated caption
            caption = self._processor.batch_decode(generated_ids, skip_special_tokens=True)[
                0
            ].strip()

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.COMPLETED,
                processing_time_ms=elapsed_ms,
                data={
                    "caption": caption,
                    "model_name": self._model_name,
                    "max_length": self._max_length,
                },
            )

        except Exception as e:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.FAILED,
                processing_time_ms=elapsed_ms,
                error_message=f"Captioning failed: {e}",
            )
