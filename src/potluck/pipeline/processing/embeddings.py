"""Embedding processors for text and media content.

This module provides processors that generate semantic embeddings for:
- Text entities (ChatMessage, Email, SocialPost, SocialComment, KnowledgeNote)
- Media entities (CLIP visual embeddings, OCR/caption text embeddings)

Embeddings enable semantic search, similarity detection, and clustering.
"""

import time
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

import torch
from celery import Task
from celery.exceptions import Retry
from PIL import Image
from sentence_transformers import SentenceTransformer
from sqlmodel import Session, SQLModel
from transformers import CLIPModel, CLIPProcessor

from potluck.core.celery import (
    MAX_RETRIES,
    RETRY_BACKOFF,
    RETRY_BACKOFF_MAX,
    celery_app,
)
from potluck.core.logging import get_logger
from potluck.models.base import EntityType
from potluck.models.media import EmbeddingType, Media, MediaEmbedding, MediaType
from potluck.models.messages import ChatMessage
from potluck.models.notes import KnowledgeNote
from potluck.pipeline.dtos import StageResult, StageStatus
from potluck.pipeline.processing.base import BaseProcessor, run_processor_task
from potluck.pipeline.processing.registry import ProcessorRegistry

logger = get_logger(__name__)


# Default embedding model for text
DEFAULT_TEXT_MODEL = "all-MiniLM-L6-v2"
# CLIP model for visual embeddings
DEFAULT_CLIP_MODEL = "openai/clip-vit-base-patch32"


# Maps entity type to the field containing text content
TEXT_FIELD_MAP: dict[EntityType, str] = {
    EntityType.CHAT_MESSAGE: "content",
    EntityType.EMAIL: "body_text",
    EntityType.SOCIAL_POST: "body",
    EntityType.SOCIAL_COMMENT: "body",
    EntityType.KNOWLEDGE_NOTE: "content",
}


@ProcessorRegistry.register(priority=60)
class TextEmbeddingProcessor(BaseProcessor):
    """Processor for generating text embeddings using sentence-transformers.

    Generates semantic embeddings for text entities to enable:
    - Semantic search across all text content
    - Similar content discovery
    - Clustering related messages/posts

    Uses all-MiniLM-L6-v2 by default (384 dimensions, fast and accurate).
    """

    NAME: ClassVar[str] = "text_embedding"
    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {
        EntityType.CHAT_MESSAGE,
        EntityType.EMAIL,
        EntityType.SOCIAL_POST,
        EntityType.SOCIAL_COMMENT,
        EntityType.KNOWLEDGE_NOTE,
    }
    # KnowledgeNote has an inline embedding field; others need different handling
    PERSIST_FIELDS: ClassVar[list[str]] = []  # Custom persist_result

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_TEXT_MODEL,
        device: str | None = None,
    ) -> None:
        """Initialize the text embedding processor.

        Args:
            model_name: Sentence-transformers model name.
            device: Device to run model on ('cuda', 'cpu', or None for auto).
        """
        self._model_name = model_name
        self._device = device
        self._model: SentenceTransformer | None = None

    def _load_model(self) -> None:
        """Lazy load the sentence-transformers model."""
        if self._model is not None:
            return

        device = self._device
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        logger.info(f"Loading sentence-transformer model: {self._model_name} on {device}")
        self._model = SentenceTransformer(self._model_name, device=device)

    def _get_text_content(self, entity: SQLModel, entity_type: EntityType) -> str | None:
        """Extract text content from an entity based on its type."""
        field_name = TEXT_FIELD_MAP.get(entity_type)
        if field_name is None:
            return None

        content = getattr(entity, field_name, None)
        if not content or not isinstance(content, str):
            return None

        # For emails, include subject if available
        if entity_type == EntityType.EMAIL:
            subject = getattr(entity, "subject", None)
            if subject:
                content = f"{subject}\n\n{content}"

        # For social posts, include title if available
        if entity_type == EntityType.SOCIAL_POST:
            title = getattr(entity, "title", None)
            if title:
                content = f"{title}\n\n{content}"

        return content.strip() if content else None

    def should_execute(self, entity: SQLModel) -> bool:
        """Check if entity has text content to embed."""
        # We need to know the entity type to find the text field
        # This will be determined in execute() via the entity type
        return True

    def execute(self, entity: SQLModel) -> StageResult:
        """Generate text embedding for an entity.

        Args:
            entity: The entity to process.

        Returns:
            StageResult with the generated embedding.
        """
        start_time = time.monotonic()

        # Determine entity type from the entity class
        entity_type = self._get_entity_type(entity)
        entity_id = getattr(entity, "id", None)
        if entity_type is None or entity_id is None:
            return StageResult(
                item_id=entity_id,  # type: ignore[arg-type]
                stage_name=self.NAME,
                status=StageStatus.SKIPPED,
                error_message="Unknown entity type or missing ID",
            )

        # Get text content
        text = self._get_text_content(entity, entity_type)
        if not text:
            return StageResult(
                item_id=entity.id,  # type: ignore[attr-defined]
                stage_name=self.NAME,
                status=StageStatus.SKIPPED,
                error_message="No text content to embed",
            )

        try:
            self._load_model()
            assert self._model is not None

            # Generate embedding
            embedding = self._model.encode(text, convert_to_numpy=True)
            embedding_list: list[float] = embedding.tolist()

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            return StageResult(
                item_id=entity.id,  # type: ignore[attr-defined]
                stage_name=self.NAME,
                status=StageStatus.COMPLETED,
                processing_time_ms=elapsed_ms,
                data={
                    "embedding": embedding_list,
                    "embedding_dim": len(embedding_list),
                    "model_name": self._model_name,
                    "text_length": len(text),
                },
            )

        except Exception as e:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            logger.exception(f"Text embedding failed: {e}")
            return StageResult(
                item_id=entity.id,  # type: ignore[attr-defined]
                stage_name=self.NAME,
                status=StageStatus.FAILED,
                processing_time_ms=elapsed_ms,
                error_message=f"Text embedding failed: {e}",
            )

    def _get_entity_type(self, entity: SQLModel) -> EntityType | None:
        """Determine EntityType from entity instance."""
        if isinstance(entity, ChatMessage):
            return EntityType.CHAT_MESSAGE
        if isinstance(entity, KnowledgeNote):
            return EntityType.KNOWLEDGE_NOTE
        # Import here to avoid circular imports
        from potluck.models.email import Email
        from potluck.models.social import SocialComment, SocialPost

        if isinstance(entity, Email):
            return EntityType.EMAIL
        if isinstance(entity, SocialPost):
            return EntityType.SOCIAL_POST
        if isinstance(entity, SocialComment):
            return EntityType.SOCIAL_COMMENT
        return None

    def persist_result(
        self,
        session: Session,
        entity_type: EntityType,
        entity_id: str,
        result: StageResult,
    ) -> dict[str, Any]:
        """Persist embedding to the entity.

        For KnowledgeNote, stores in the inline embedding field.
        For other entities, embeddings are returned but not persisted
        (would require schema changes to add embedding fields).

        Args:
            session: Database session.
            entity_type: The entity type.
            entity_id: The entity ID.
            result: The StageResult from execute().

        Returns:
            Dict with task result summary.
        """
        embedding = result.data.get("embedding")

        # Only KnowledgeNote has an inline embedding field currently
        if entity_type == EntityType.KNOWLEDGE_NOTE and embedding:
            from potluck.pipeline.processing.base import _get_entity

            entity = _get_entity(session, entity_type, entity_id)
            if entity and isinstance(entity, KnowledgeNote):
                entity.embedding = embedding
                session.add(entity)
                session.commit()
                logger.debug(f"Persisted embedding for KnowledgeNote {entity_id}")

        return {
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "status": result.status.value,
            "embedding_dim": result.data.get("embedding_dim"),
            "processing_time_ms": result.processing_time_ms,
        }


@ProcessorRegistry.register(priority=70)
class MediaEmbeddingProcessor(BaseProcessor):
    """Processor for generating media embeddings using CLIP and text models.

    Generates multiple embedding types for media items:
    - CLIP: Visual embedding from the image itself
    - OCR: Text embedding from OCR-extracted text (if available)
    - CAPTION: Text embedding from AI-generated caption (if available)

    These embeddings enable cross-modal search (text-to-image, image-to-image).
    """

    NAME: ClassVar[str] = "media_embedding"
    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {EntityType.MEDIA}
    PERSIST_FIELDS: ClassVar[list[str]] = []  # Custom persist_result for MediaEmbedding

    def __init__(
        self,
        *,
        clip_model_name: str = DEFAULT_CLIP_MODEL,
        text_model_name: str = DEFAULT_TEXT_MODEL,
        device: str | None = None,
        generate_clip: bool = True,
        generate_ocr_embedding: bool = True,
        generate_caption_embedding: bool = True,
    ) -> None:
        """Initialize the media embedding processor.

        Args:
            clip_model_name: HuggingFace CLIP model name for visual embeddings.
            text_model_name: Text model for OCR/caption embeddings.
            device: Device to run models on.
            generate_clip: Whether to generate CLIP embeddings.
            generate_ocr_embedding: Whether to embed OCR text.
            generate_caption_embedding: Whether to embed captions.
        """
        self._clip_model_name = clip_model_name
        self._text_model_name = text_model_name
        self._device = device
        self._generate_clip = generate_clip
        self._generate_ocr_embedding = generate_ocr_embedding
        self._generate_caption_embedding = generate_caption_embedding

        # Lazy-loaded models
        self._clip_model: CLIPModel | None = None
        self._clip_processor: CLIPProcessor | None = None
        self._text_model: SentenceTransformer | None = None
        self._torch_device: torch.device | None = None

    def _load_models(self) -> None:
        """Lazy load the embedding models."""
        device = self._device
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._torch_device = torch.device(device)

        if self._generate_clip and self._clip_model is None:
            logger.info(f"Loading CLIP model: {self._clip_model_name} on {device}")
            self._clip_processor = CLIPProcessor.from_pretrained(self._clip_model_name)
            self._clip_model = CLIPModel.from_pretrained(self._clip_model_name)
            self._clip_model.to(self._torch_device)  # type: ignore[arg-type]
            # Set model to inference mode
            self._clip_model.requires_grad_(False)

        if (
            self._generate_ocr_embedding or self._generate_caption_embedding
        ) and self._text_model is None:
            logger.info(f"Loading text model: {self._text_model_name} on {device}")
            self._text_model = SentenceTransformer(self._text_model_name, device=device)

    def should_execute(self, entity: SQLModel) -> bool:
        """Only process images (for now)."""
        media: Media = entity  # type: ignore[assignment]
        return media.media_type == MediaType.IMAGE

    def execute(self, entity: SQLModel) -> StageResult:
        """Generate embeddings for a media item.

        Args:
            entity: The media entity to process.

        Returns:
            StageResult with generated embeddings.
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
            self._load_models()

            embeddings: dict[str, dict[str, Any]] = {}

            # Generate CLIP embedding
            if self._generate_clip and self._clip_model and self._clip_processor:
                img = Image.open(file_path).convert("RGB")
                inputs = self._clip_processor(images=img, return_tensors="pt")
                inputs = {k: v.to(self._torch_device) for k, v in inputs.items()}

                with torch.no_grad():
                    image_features = self._clip_model.get_image_features(**inputs)
                    # Normalize the embedding
                    image_features = image_features / image_features.norm(dim=-1, keepdim=True)
                    clip_embedding = image_features.cpu().numpy().flatten().tolist()

                embeddings["clip"] = {
                    "embedding": clip_embedding,
                    "model_name": self._clip_model_name,
                    "embedding_type": EmbeddingType.CLIP.value,
                }

            # Generate OCR text embedding
            if self._generate_ocr_embedding and media.ocr_text and self._text_model:
                ocr_embedding = self._text_model.encode(media.ocr_text, convert_to_numpy=True)
                embeddings["ocr"] = {
                    "embedding": ocr_embedding.tolist(),
                    "model_name": self._text_model_name,
                    "embedding_type": EmbeddingType.OCR.value,
                }

            # Generate caption embedding
            if self._generate_caption_embedding and media.caption and self._text_model:
                caption_embedding = self._text_model.encode(media.caption, convert_to_numpy=True)
                embeddings["caption"] = {
                    "embedding": caption_embedding.tolist(),
                    "model_name": self._text_model_name,
                    "embedding_type": EmbeddingType.CAPTION.value,
                }

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.COMPLETED,
                processing_time_ms=elapsed_ms,
                data={
                    "embeddings": embeddings,
                    "embedding_count": len(embeddings),
                },
            )

        except Exception as e:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            logger.exception(f"Media embedding failed for {media.file_path}: {e}")
            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.FAILED,
                processing_time_ms=elapsed_ms,
                error_message=f"Media embedding failed: {e}",
            )

    def persist_result(
        self,
        session: Session,
        entity_type: EntityType,
        entity_id: str,
        result: StageResult,
    ) -> dict[str, Any]:
        """Persist embeddings to MediaEmbedding records.

        Args:
            session: Database session.
            entity_type: The entity type (should be MEDIA).
            entity_id: The entity ID.
            result: The StageResult from execute().

        Returns:
            Dict with task result summary.
        """
        embeddings_data = result.data.get("embeddings", {})
        persisted_count = 0

        for _embed_key, embed_data in embeddings_data.items():
            embedding_type_str = embed_data.get("embedding_type")
            if not embedding_type_str:
                continue

            embedding_type = EmbeddingType(embedding_type_str)
            embedding_vec = embed_data.get("embedding")
            model_name = embed_data.get("model_name", "unknown")

            if embedding_vec:
                media_embedding = MediaEmbedding(
                    media_id=UUID(entity_id),
                    embedding_type=embedding_type,
                    model_name=model_name,
                    embedding=embedding_vec,
                )
                session.add(media_embedding)
                persisted_count += 1

        if persisted_count > 0:
            session.commit()
            logger.debug(f"Persisted {persisted_count} embeddings for media {entity_id}")

        return {
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "status": result.status.value,
            "embeddings_persisted": persisted_count,
            "processing_time_ms": result.processing_time_ms,
        }


# -----------------------------------------------------------------------------
# Celery Tasks
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
def run_text_embedding_processor(
    self: "Task[..., dict[str, Any]]",
    entity_type: str,
    entity_id: str,
) -> dict[str, Any]:
    """Generate text embedding for an entity."""
    return run_processor_task(self, EntityType(entity_type), entity_id, TextEmbeddingProcessor)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_media_embedding_processor(
    self: "Task[..., dict[str, Any]]",
    entity_type: str,
    entity_id: str,
) -> dict[str, Any]:
    """Generate media embeddings (CLIP, OCR, caption) for a media item."""
    return run_processor_task(self, EntityType(entity_type), entity_id, MediaEmbeddingProcessor)


# Register tasks with their processors
ProcessorRegistry.set_task(TextEmbeddingProcessor.NAME, run_text_embedding_processor)
ProcessorRegistry.set_task(MediaEmbeddingProcessor.NAME, run_media_embedding_processor)
