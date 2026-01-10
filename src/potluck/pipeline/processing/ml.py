"""Centralized ML model loading with lazy initialization and caching.

This module provides standardized utilities for loading ML models across processors:
- `get_device()`: Returns appropriate compute device, respecting GPU env var
- `MLModels`: Class with lazy-loaded model getters and cross-instance caching

All models are cached at the class level to avoid reloading across processor instances.
"""

import os
from typing import Any, ClassVar

import torch
from PIL import Image
from sentence_transformers import SentenceTransformer
from transformers import (
    AutoModel,
    AutoProcessor,
    Blip2ForConditionalGeneration,
    Blip2Processor,
    PreTrainedModel,
)

from potluck.core.logging import get_logger
from potluck.pipeline.processing._arcface import download_weights, get_weights_path, iresnet50

logger = get_logger(__name__)


# Default model identifiers
DEFAULT_TEXT_EMBEDDING_MODEL = "intfloat/e5-small-v2"
DEFAULT_MULTIMODAL_MODEL = "google/siglip-base-patch16-224"
DEFAULT_CAPTIONING_MODEL = "Salesforce/blip2-opt-2.7b"


def get_device(preferred: str | None = None) -> torch.device:
    """Get compute device, respecting GPU env var from .env.

    Device selection priority:
    1. If `preferred` is specified, use that device directly
    2. Check GPU env var (from .env via Docker): only use CUDA if GPU=true AND available
    3. Default to CPU

    Args:
        preferred: Explicit device preference ('cuda', 'cpu', or specific 'cuda:0').

    Returns:
        torch.device for model placement.
    """
    if preferred:
        return torch.device(preferred)

    # Check GPU env var (from .env via Docker)
    gpu_enabled = os.getenv("GPU", "false").lower() == "true"

    if gpu_enabled and torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


class MLModels:
    """Centralized ML model loading with lazy initialization and caching.

    Models are cached at the class level so multiple processor instances share
    the same loaded models. This avoids redundant model loading in Celery workers.

    Example:
        models = MLModels()
        text_model = models.get_text_embedding_model()
        siglip_model, processor = models.get_siglip_model()
    """

    # Class-level cache for model sharing across instances
    _cache: ClassVar[dict[str, Any]] = {}
    _lock_initialized: ClassVar[bool] = False

    def __init__(self, device: str | None = None) -> None:
        """Initialize MLModels with device configuration.

        Args:
            device: Explicit device preference, or None to auto-detect.
        """
        self.device = get_device(device)
        logger.debug(f"MLModels initialized with device: {self.device}")

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the model cache. Useful for testing."""
        cls._cache.clear()
        logger.info("MLModels cache cleared")

    # -------------------------------------------------------------------------
    # Text Embedding Models
    # -------------------------------------------------------------------------

    def get_text_embedding_model(
        self,
        model_name: str = DEFAULT_TEXT_EMBEDDING_MODEL,
    ) -> SentenceTransformer:
        """Get text embedding model (sentence-transformers).

        Uses e5-small-v2 by default, which generates 384-dimensional embeddings
        optimized for text-to-text semantic search.

        Note: e5 models require prefixing input text with "query: " or "passage: "
        depending on use case. The caller is responsible for adding this prefix.

        Args:
            model_name: HuggingFace model identifier.

        Returns:
            Loaded SentenceTransformer model.
        """
        cache_key = f"text_embedding:{model_name}:{self.device}"
        if cache_key not in self._cache:
            logger.info(f"Loading text embedding model: {model_name} on {self.device}")
            self._cache[cache_key] = SentenceTransformer(model_name, device=str(self.device))
        model: SentenceTransformer = self._cache[cache_key]
        return model

    # -------------------------------------------------------------------------
    # Multimodal Models (SigLIP)
    # -------------------------------------------------------------------------

    def get_siglip_model(
        self,
        model_name: str = DEFAULT_MULTIMODAL_MODEL,
    ) -> tuple[PreTrainedModel, Any]:
        """Get SigLIP model and processor for multimodal embeddings.

        SigLIP generates 768-dimensional embeddings in a shared text-image space,
        enabling cross-modal search (text queries finding images and vice versa).

        Args:
            model_name: HuggingFace model identifier.

        Returns:
            Tuple of (model, processor).
        """
        cache_key = f"siglip:{model_name}:{self.device}"
        if cache_key not in self._cache:
            logger.info(f"Loading SigLIP model: {model_name} on {self.device}")
            processor = AutoProcessor.from_pretrained(model_name)  # type: ignore[no-untyped-call]
            model = AutoModel.from_pretrained(model_name)
            model.to(self.device)
            model.eval()
            model.requires_grad_(False)
            self._cache[cache_key] = (model, processor)
        result: tuple[PreTrainedModel, Any] = self._cache[cache_key]
        return result

    def encode_image_siglip(
        self,
        image: Image.Image,
        model_name: str = DEFAULT_MULTIMODAL_MODEL,
        normalize: bool = True,
    ) -> list[float]:
        """Encode an image using SigLIP.

        Args:
            image: PIL Image to encode.
            model_name: SigLIP model to use.
            normalize: Whether to L2-normalize the embedding.

        Returns:
            768-dimensional embedding vector.
        """
        model, processor = self.get_siglip_model(model_name)
        inputs = processor(images=image, return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = model.get_image_features(**inputs)  # type: ignore[operator]
            if normalize:
                outputs = torch.nn.functional.normalize(outputs, dim=-1)
            result: list[float] = outputs.cpu().numpy().flatten().tolist()
            return result

    def encode_text_siglip(
        self,
        text: str,
        model_name: str = DEFAULT_MULTIMODAL_MODEL,
        normalize: bool = True,
    ) -> list[float]:
        """Encode text using SigLIP for cross-modal search.

        Args:
            text: Text to encode.
            model_name: SigLIP model to use.
            normalize: Whether to L2-normalize the embedding.

        Returns:
            768-dimensional embedding vector in the same space as image embeddings.
        """
        model, processor = self.get_siglip_model(model_name)
        inputs = processor(text=text, return_tensors="pt", padding=True)
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = model.get_text_features(**inputs)  # type: ignore[operator]
            if normalize:
                outputs = torch.nn.functional.normalize(outputs, dim=-1)
            result: list[float] = outputs.cpu().numpy().flatten().tolist()
            return result

    # -------------------------------------------------------------------------
    # Captioning Models (BLIP-2)
    # -------------------------------------------------------------------------

    def get_blip2_model(
        self,
        model_name: str = DEFAULT_CAPTIONING_MODEL,
    ) -> tuple[Blip2ForConditionalGeneration, Blip2Processor]:
        """Get BLIP-2 model and processor for image captioning.

        Args:
            model_name: HuggingFace model identifier.

        Returns:
            Tuple of (model, processor).
        """
        cache_key = f"blip2:{model_name}:{self.device}"
        if cache_key not in self._cache:
            logger.info(f"Loading BLIP-2 model: {model_name} on {self.device}")
            processor = Blip2Processor.from_pretrained(model_name)

            if self.device.type == "cuda":
                model = Blip2ForConditionalGeneration.from_pretrained(
                    model_name,
                    torch_dtype=torch.float16,
                    device_map="auto",
                )
            else:
                model = Blip2ForConditionalGeneration.from_pretrained(model_name)
                model.to(self.device)

            self._cache[cache_key] = (model, processor)
        result: tuple[Blip2ForConditionalGeneration, Blip2Processor] = self._cache[cache_key]
        return result

    # -------------------------------------------------------------------------
    # Face Recognition Models (ArcFace)
    # -------------------------------------------------------------------------

    def get_arcface_model(self) -> torch.nn.Module:
        """Get ArcFace IResNet50 model for face embeddings.

        Automatically downloads pretrained weights on first use.

        Returns:
            Loaded ArcFace model ready for inference.
        """
        cache_key = f"arcface:iresnet50:{self.device}"
        if cache_key not in self._cache:
            logger.info(f"Loading ArcFace IResNet50 on {self.device}")
            model = iresnet50(num_features=512)

            # Ensure weights are downloaded
            weights_path = get_weights_path()
            if not weights_path.exists():
                logger.info("Downloading ArcFace weights (first time setup)...")
                download_weights()

            if not weights_path.exists():
                raise RuntimeError(
                    f"ArcFace weights not found at {weights_path}. "
                    "Run: python -c 'from potluck.pipeline.processing._arcface import download_weights; download_weights()'"
                )

            state_dict = torch.load(weights_path, map_location=self.device, weights_only=True)

            # Handle checkpoint format variations
            if any(k.startswith("arcface.") for k in state_dict):
                state_dict = {
                    k.replace("arcface.", ""): v
                    for k, v in state_dict.items()
                    if k.startswith("arcface.")
                }

            model.load_state_dict(state_dict, strict=False)
            model.to(self.device)
            model.eval()
            model.requires_grad_(False)

            logger.info(f"Loaded ArcFace weights from {weights_path}")
            self._cache[cache_key] = model

        result: torch.nn.Module = self._cache[cache_key]
        return result

    # -------------------------------------------------------------------------
    # OCR Models (EasyOCR)
    # -------------------------------------------------------------------------

    def get_easyocr_reader(
        self,
        languages: list[str] | None = None,
    ) -> Any:
        """Get EasyOCR reader for text extraction.

        Args:
            languages: List of language codes (default: ['en']).

        Returns:
            EasyOCR Reader instance.
        """
        import easyocr

        if languages is None:
            languages = ["en"]

        # Create cache key from sorted languages for consistency
        lang_key = ",".join(sorted(languages))
        gpu = self.device.type == "cuda"
        cache_key = f"easyocr:{lang_key}:{gpu}"

        if cache_key not in self._cache:
            logger.info(f"Loading EasyOCR with languages: {languages}, GPU: {gpu}")
            self._cache[cache_key] = easyocr.Reader(languages, gpu=gpu)

        return self._cache[cache_key]

    # -------------------------------------------------------------------------
    # Face Detection (MTCNN)
    # -------------------------------------------------------------------------

    def get_mtcnn(self) -> Any:
        """Get MTCNN face detector.

        Returns:
            MTCNN instance configured for the current device.
        """
        from facenet_pytorch import MTCNN

        cache_key = f"mtcnn:{self.device}"
        if cache_key not in self._cache:
            logger.info(f"Loading MTCNN face detector on {self.device}")
            self._cache[cache_key] = MTCNN(
                keep_all=True,
                device=self.device,
                post_process=False,  # Return raw crops for ArcFace preprocessing
            )
        return self._cache[cache_key]
