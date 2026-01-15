"""Centralized ML model loading with lazy initialization and caching.

This module provides standardized utilities for loading ML models across processors:
- `get_device()`: Returns appropriate compute device, respecting GPU env var
- `MLModels`: Class with lazy-loaded model getters and cross-instance caching

All models are cached at the class level to avoid reloading across processor instances.
"""

import os
from typing import Any, ClassVar

import easyocr
import torch
from facenet_pytorch import MTCNN
from PIL import Image
from sentence_transformers import SentenceTransformer
from transformers import (
    AutoModel,
    AutoProcessor,
    Blip2ForConditionalGeneration,
    Blip2Processor,
    PreTrainedModel,
)

from potluck.core.constants import (
    DEFAULT_CAPTIONING_MODEL,
    DEFAULT_MULTIMODAL_MODEL,
    DEFAULT_TEXT_EMBEDDING_MODEL,
    FACE_EMBEDDING_DIM,
)
from potluck.core.logging import get_logger
from potluck.pipeline.processing._arcface import download_weights, get_weights_path, iresnet50

logger = get_logger(__name__)


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
        text_model = models.get_text_encoder()
        multimodal_model, processor = models.get_multimodal_encoder()
    """

    # Class-level cache for model sharing across instances
    _cache: ClassVar[dict[str, Any]] = {}

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

    def download_all_models(self) -> None:
        """Pre-download all ML models for offline use.

        This eagerly loads all models into memory, triggering downloads from
        HuggingFace Hub if not already cached locally. Useful for container
        startup to ensure all models are available before processing begins.
        """
        logger.info("Pre-downloading all ML models...")

        # Text embedding (e5-small-v2, ~90MB)
        logger.info("Loading text encoder...")
        self.get_text_encoder()

        # Multimodal (SigLIP, ~380MB)
        logger.info("Loading multimodal encoder...")
        self.get_multimodal_encoder()

        # Face detection (MTCNN)
        logger.info("Loading face detector...")
        self.get_face_detector()

        # Face recognition (ArcFace, ~250MB)
        logger.info("Loading face encoder...")
        self.get_face_encoder()

        # OCR (EasyOCR, ~100MB)
        logger.info("Loading OCR reader...")
        self.get_ocr_reader()

        # Captioning (BLIP-2, ~2.7GB)
        logger.info("Loading captioning model...")
        self.get_captioning_model()

        logger.info("All models downloaded successfully")

    # -------------------------------------------------------------------------
    # Text Embedding Models
    # -------------------------------------------------------------------------

    def get_text_encoder(
        self,
        model_name: str = DEFAULT_TEXT_EMBEDDING_MODEL,
    ) -> SentenceTransformer:
        """Get text encoder model (sentence-transformers).

        Uses e5-small-v2 by default, which generates TEXT_EMBEDDING_DIM (384)
        dimensional embeddings optimized for text-to-text semantic search.

        Note: e5 models require prefixing input text with "query: " or "passage: "
        depending on use case. The caller is responsible for adding this prefix.

        Args:
            model_name: HuggingFace model identifier.

        Returns:
            Loaded SentenceTransformer model.
        """
        cache_key = f"text_encoder:{model_name}:{self.device}"
        if cache_key not in self._cache:
            logger.info(f"Loading text encoder: {model_name} on {self.device}")
            self._cache[cache_key] = SentenceTransformer(model_name, device=str(self.device))
        model: SentenceTransformer = self._cache[cache_key]
        return model

    # -------------------------------------------------------------------------
    # Multimodal Models
    # -------------------------------------------------------------------------

    def get_multimodal_encoder(
        self,
        model_name: str = DEFAULT_MULTIMODAL_MODEL,
    ) -> tuple[PreTrainedModel, Any]:
        """Get multimodal encoder model and processor for cross-modal embeddings.

        Generates MULTIMODAL_EMBEDDING_DIM (768) dimensional embeddings in a shared
        text-image space, enabling cross-modal search (text queries finding images
        and vice versa).

        Args:
            model_name: HuggingFace model identifier.

        Returns:
            Tuple of (model, processor).
        """
        cache_key = f"multimodal_encoder:{model_name}:{self.device}"
        if cache_key not in self._cache:
            logger.info(f"Loading multimodal encoder: {model_name} on {self.device}")
            processor = AutoProcessor.from_pretrained(model_name)  # type: ignore[no-untyped-call]
            model = AutoModel.from_pretrained(model_name)
            model.to(self.device)
            model.train(False)  # Set to inference mode
            model.requires_grad_(False)
            self._cache[cache_key] = (model, processor)
        result: tuple[PreTrainedModel, Any] = self._cache[cache_key]
        return result

    def encode_image(
        self,
        image: Image.Image,
        model_name: str = DEFAULT_MULTIMODAL_MODEL,
        normalize: bool = True,
    ) -> list[float]:
        """Encode an image using the multimodal encoder.

        Args:
            image: PIL Image to encode.
            model_name: Multimodal model to use.
            normalize: Whether to L2-normalize the embedding.

        Returns:
            MULTIMODAL_EMBEDDING_DIM (768) dimensional embedding vector.
        """
        model, processor = self.get_multimodal_encoder(model_name)
        inputs = processor(images=image, return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = model.get_image_features(**inputs)  # type: ignore[operator]
            if normalize:
                outputs = torch.nn.functional.normalize(outputs, dim=-1)
            result: list[float] = outputs.cpu().numpy().flatten().tolist()
            return result

    def encode_text_multimodal(
        self,
        text: str,
        model_name: str = DEFAULT_MULTIMODAL_MODEL,
        normalize: bool = True,
    ) -> list[float]:
        """Encode text using the multimodal encoder for cross-modal search.

        Args:
            text: Text to encode.
            model_name: Multimodal model to use.
            normalize: Whether to L2-normalize the embedding.

        Returns:
            MULTIMODAL_EMBEDDING_DIM (768) dimensional embedding vector in the
            same space as image embeddings.
        """
        model, processor = self.get_multimodal_encoder(model_name)
        inputs = processor(text=text, return_tensors="pt", padding=True)
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = model.get_text_features(**inputs)  # type: ignore[operator]
            if normalize:
                outputs = torch.nn.functional.normalize(outputs, dim=-1)
            result: list[float] = outputs.cpu().numpy().flatten().tolist()
            return result

    # -------------------------------------------------------------------------
    # Captioning Models
    # -------------------------------------------------------------------------

    def get_captioning_model(
        self,
        model_name: str = DEFAULT_CAPTIONING_MODEL,
    ) -> tuple[Blip2ForConditionalGeneration, Blip2Processor]:
        """Get captioning model and processor for image captioning.

        Args:
            model_name: HuggingFace model identifier.

        Returns:
            Tuple of (model, processor).
        """
        cache_key = f"captioning:{model_name}:{self.device}"
        if cache_key not in self._cache:
            logger.info(f"Loading captioning model: {model_name} on {self.device}")
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
    # Face Recognition Models
    # -------------------------------------------------------------------------

    def get_face_encoder(self) -> torch.nn.Module:
        """Get face encoder model for face embeddings.

        Generates FACE_EMBEDDING_DIM (512) dimensional embeddings for face
        recognition and clustering. Automatically downloads pretrained weights
        on first use.

        Returns:
            Loaded face encoder model ready for inference.
        """
        cache_key = f"face_encoder:iresnet50:{self.device}"
        if cache_key not in self._cache:
            logger.info(f"Loading face encoder on {self.device}")
            model = iresnet50(num_features=FACE_EMBEDDING_DIM)

            # Ensure weights are downloaded
            weights_path = get_weights_path()
            if not weights_path.exists():
                logger.info("Downloading face encoder weights (first time setup)...")
                download_weights()

            if not weights_path.exists():
                raise RuntimeError(
                    f"Face encoder weights not found at {weights_path}. "
                    "Run: potluck download-models"
                )

            state_dict = torch.load(weights_path, map_location=self.device, weights_only=True)

            # Handle checkpoint format variations
            if any(k.startswith("arcface.") for k in state_dict):
                state_dict = {
                    k.replace("arcface.", ""): v
                    for k, v in state_dict.items()
                    if k.startswith("arcface.")
                }

            # Load weights with validation
            missing_keys, unexpected_keys = model.load_state_dict(state_dict, strict=False)
            if missing_keys:
                logger.warning(
                    f"Face encoder checkpoint missing {len(missing_keys)} keys: "
                    f"{missing_keys[:5]}{'...' if len(missing_keys) > 5 else ''}"
                )
            if unexpected_keys:
                logger.warning(
                    f"Face encoder checkpoint has {len(unexpected_keys)} unexpected keys: "
                    f"{unexpected_keys[:5]}{'...' if len(unexpected_keys) > 5 else ''}"
                )
            if len(missing_keys) > 10:
                raise RuntimeError(
                    f"Face encoder checkpoint incompatible: {len(missing_keys)} missing keys. "
                    "Delete ~/.cache/potluck/models/arcface/ and run: potluck download-models"
                )

            model.to(self.device)
            model.train(False)  # Set to inference mode
            model.requires_grad_(False)

            logger.info(f"Loaded face encoder weights from {weights_path}")
            self._cache[cache_key] = model

        result: torch.nn.Module = self._cache[cache_key]
        return result

    # -------------------------------------------------------------------------
    # OCR Models
    # -------------------------------------------------------------------------

    def get_ocr_reader(
        self,
        languages: list[str] | None = None,
    ) -> easyocr.Reader:
        """Get OCR reader for text extraction from images.

        Args:
            languages: List of language codes (default: ['en']).

        Returns:
            OCR Reader instance.
        """
        if languages is None:
            languages = ["en"]

        # Create cache key from sorted languages for consistency
        lang_key = ",".join(sorted(languages))
        gpu = self.device.type == "cuda"
        cache_key = f"ocr_reader:{lang_key}:{gpu}"

        if cache_key not in self._cache:
            logger.info(f"Loading OCR reader with languages: {languages}, GPU: {gpu}")
            self._cache[cache_key] = easyocr.Reader(languages, gpu=gpu)

        return self._cache[cache_key]

    # -------------------------------------------------------------------------
    # Face Detection
    # -------------------------------------------------------------------------

    def get_face_detector(self) -> MTCNN:
        """Get face detector for locating faces in images.

        Returns:
            Face detector instance configured for the current device.
        """
        cache_key = f"face_detector:{self.device}"
        if cache_key not in self._cache:
            logger.info(f"Loading face detector on {self.device}")
            self._cache[cache_key] = MTCNN(
                keep_all=True,
                device=self.device,
                post_process=False,  # Return raw crops for face encoder preprocessing
            )
        return self._cache[cache_key]
