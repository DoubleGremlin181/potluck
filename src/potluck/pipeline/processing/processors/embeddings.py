"""Embedding processors for text and media content.

This module provides processors that generate semantic embeddings for:
- Text entities (ChatMessage, Email, SocialPost, SocialComment, KnowledgeNote)
- Media entities (SigLIP visual embeddings)

Embedding Strategy:
- Text entities get TWO embeddings:
  1. `embedding` (384d) - e5-small-v2 text-only for text-to-text semantic search
  2. `multimodal_embedding` (768d) - SigLIP text for cross-modal text↔image search
- Image entities get ONE embedding:
  1. `embedding` (768d) - SigLIP image encoder (same space as text multimodal)

This enables:
- Pure text search: "find messages about vacation" → matches text entities
- Cross-modal search: "find images of beaches" → matches images via multimodal
"""

import time
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID, uuid4

from celery import Task
from celery.exceptions import Retry
from PIL import Image
from sqlmodel import Session, SQLModel

from potluck.core.celery import (
    MAX_RETRIES,
    RETRY_BACKOFF,
    RETRY_BACKOFF_MAX,
    celery_app,
)
from potluck.core.constants import (
    DEFAULT_MULTIMODAL_MODEL,
    DEFAULT_TEXT_EMBEDDING_MODEL,
)
from potluck.core.logging import get_logger
from potluck.models.base import EntityType
from potluck.models.documents import Document
from potluck.models.media import EmbeddingType, Media, MediaEmbedding, MediaType
from potluck.models.messages import ChatMessage
from potluck.models.notes import KnowledgeNote
from potluck.pipeline.dtos import BatchStageResult, StageResult, StageStatus
from potluck.pipeline.processing.core.base import (
    BaseProcessor,
    _get_entity,
    run_batch_processor_task,
    run_processor_task,
)
from potluck.pipeline.processing.core.ml import MLModels
from potluck.pipeline.processing.core.registry import ProcessorRegistry

logger = get_logger(__name__)


# Maps entity type to the field containing text content
TEXT_FIELD_MAP: dict[EntityType, str] = {
    EntityType.CHAT_MESSAGE: "content",
    EntityType.EMAIL: "body_text",
    EntityType.SOCIAL_POST: "body",
    EntityType.SOCIAL_COMMENT: "body",
    EntityType.KNOWLEDGE_NOTE: "content",
    EntityType.DOCUMENT: "content",
}


@ProcessorRegistry.register(priority=25)
class TextEmbeddingProcessor(BaseProcessor):
    """Processor for generating text embeddings using e5-small-v2.

    Generates 384-dimensional text-only embeddings optimized for text-to-text
    semantic search. These are stored in the `embedding` field.

    Note: e5 models require prefixing input with "passage: " for documents
    and "query: " for search queries. This processor uses "passage: " prefix.
    """

    NAME: ClassVar[str] = "text_embedding"
    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {
        EntityType.CHAT_MESSAGE,
        EntityType.EMAIL,
        EntityType.SOCIAL_POST,
        EntityType.SOCIAL_COMMENT,
        EntityType.KNOWLEDGE_NOTE,
        EntityType.DOCUMENT,
    }
    PERSIST_FIELDS: ClassVar[list[str]] = []  # Custom persist_result

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_TEXT_EMBEDDING_MODEL,
        device: str | None = None,
    ) -> None:
        """Initialize the text embedding processor.

        Args:
            model_name: Sentence-transformers model name.
            device: Device to run model on ('cuda', 'cpu', or None for auto).
        """
        self._model_name = model_name
        self._models = MLModels(device=device)

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

    def execute(self, entity: SQLModel) -> StageResult:
        """Generate text embedding for an entity.

        Args:
            entity: The entity to process.

        Returns:
            StageResult with the generated embedding.
        """
        start_time = time.monotonic()

        entity_type = self._get_entity_type(entity)
        entity_id: UUID | None = getattr(entity, "id", None)
        if entity_type is None or entity_id is None:
            return StageResult(
                item_id=entity_id or uuid4(),
                stage_name=self.NAME,
                status=StageStatus.SKIPPED,
                error_message="Unknown entity type or missing ID",
            )

        text = self._get_text_content(entity, entity_type)
        if not text:
            return StageResult(
                item_id=entity_id,
                stage_name=self.NAME,
                status=StageStatus.SKIPPED,
                error_message="No text content to embed",
            )

        try:
            model = self._models.get_text_encoder(self._model_name)

            # e5 models require "passage: " prefix for documents
            text_with_prefix = f"passage: {text}"
            embedding = model.encode(
                text_with_prefix,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            embedding_list: list[float] = embedding.tolist()

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            return StageResult(
                item_id=entity_id,
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
                item_id=entity_id,
                stage_name=self.NAME,
                status=StageStatus.FAILED,
                processing_time_ms=elapsed_ms,
                error_message=f"Text embedding failed: {e}",
            )

    def execute_batch(self, entities: list[SQLModel]) -> BatchStageResult:
        """Process a batch of entities with vectorized encoding.

        Uses sentence-transformers batch encoding for efficiency.

        Args:
            entities: List of entities to process.

        Returns:
            BatchStageResult with individual results.
        """
        results: list[StageResult] = []
        valid_entities: list[tuple[SQLModel, EntityType, str]] = []
        start_time = time.monotonic()

        # Pre-process: extract text and filter valid entities
        for entity in entities:
            entity_type = self._get_entity_type(entity)
            entity_id: UUID | None = getattr(entity, "id", None)

            if entity_type is None or entity_id is None:
                results.append(
                    StageResult(
                        item_id=entity_id or uuid4(),
                        stage_name=self.NAME,
                        status=StageStatus.SKIPPED,
                        error_message="Unknown entity type or missing ID",
                    )
                )
                continue

            text = self._get_text_content(entity, entity_type)
            if not text:
                results.append(
                    StageResult(
                        item_id=entity_id,
                        stage_name=self.NAME,
                        status=StageStatus.SKIPPED,
                        error_message="No text content to embed",
                    )
                )
                continue

            valid_entities.append((entity, entity_type, text))

        # Batch encode if we have valid entities
        if valid_entities:
            try:
                model = self._models.get_text_encoder(self._model_name)

                # Add e5 prefix to all texts
                texts_with_prefix = [f"passage: {text}" for _, _, text in valid_entities]

                # Batch encode
                embeddings = model.encode(
                    texts_with_prefix,
                    batch_size=32,
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )

                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                per_entity_ms = elapsed_ms // len(valid_entities) if valid_entities else 0

                for i, (entity, _entity_type, text) in enumerate(valid_entities):
                    embedding_list: list[float] = embeddings[i].tolist()
                    results.append(
                        StageResult(
                            item_id=entity.id,  # type: ignore[attr-defined]
                            stage_name=self.NAME,
                            status=StageStatus.COMPLETED,
                            processing_time_ms=per_entity_ms,
                            data={
                                "embedding": embedding_list,
                                "embedding_dim": len(embedding_list),
                                "model_name": self._model_name,
                                "text_length": len(text),
                            },
                        )
                    )

            except RuntimeError as e:
                # Check for CUDA OOM - fall back to individual processing
                if "out of memory" in str(e).lower():
                    logger.warning(
                        f"Batch text embedding OOM with {len(valid_entities)} items, "
                        "falling back to individual processing"
                    )
                    for entity, _entity_type, _text in valid_entities:
                        individual_result = self.execute(entity)
                        results.append(individual_result)
                else:
                    raise
            except Exception as e:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                logger.exception(f"Batch text embedding failed: {e}")
                for entity, _, _ in valid_entities:
                    results.append(
                        StageResult(
                            item_id=entity.id,  # type: ignore[attr-defined]
                            stage_name=self.NAME,
                            status=StageStatus.FAILED,
                            processing_time_ms=elapsed_ms,
                            error_message=f"Batch embedding failed: {e}",
                        )
                    )

        return BatchStageResult(
            stage_name=self.NAME,
            total=len(entities),
            completed=sum(1 for r in results if r.status == StageStatus.COMPLETED),
            failed=sum(1 for r in results if r.status == StageStatus.FAILED),
            skipped=sum(1 for r in results if r.status == StageStatus.SKIPPED),
            results=results,
        )

    def _get_entity_type(self, entity: SQLModel) -> EntityType | None:
        """Determine EntityType from entity instance."""
        if isinstance(entity, ChatMessage):
            return EntityType.CHAT_MESSAGE
        if isinstance(entity, KnowledgeNote):
            return EntityType.KNOWLEDGE_NOTE
        if isinstance(entity, Document):
            return EntityType.DOCUMENT
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
        """Persist embedding to the entity's embedding field.

        Saves the text-only embedding to the `embedding` field on all
        supported text entity types.

        Args:
            session: Database session.
            entity_type: The entity type.
            entity_id: The entity ID.
            result: The StageResult from execute().

        Returns:
            Dict with task result summary.
        """
        embedding = result.data.get("embedding")

        if embedding and result.status == StageStatus.COMPLETED:
            entity = _get_entity(session, entity_type, entity_id)
            if entity is None:
                logger.warning(
                    f"Cannot persist embedding: {entity_type.value} {entity_id} not found"
                )
            elif not hasattr(entity, "embedding"):
                logger.warning(
                    f"Cannot persist embedding: {entity_type.value} lacks 'embedding' field"
                )
            else:
                entity.embedding = embedding
                session.add(entity)
                session.commit()
                logger.debug(f"Persisted text embedding for {entity_type.value} {entity_id}")

        return {
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "status": result.status.value,
            "embedding_dim": result.data.get("embedding_dim"),
            "processing_time_ms": result.processing_time_ms,
        }


@ProcessorRegistry.register(priority=26)
class MultimodalTextEmbeddingProcessor(BaseProcessor):
    """Processor for generating multimodal text embeddings using SigLIP.

    Generates 768-dimensional embeddings in the same space as image embeddings,
    enabling cross-modal search (text queries finding images). These are stored
    in the `multimodal_embedding` field.
    """

    NAME: ClassVar[str] = "multimodal_text_embedding"
    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {
        EntityType.CHAT_MESSAGE,
        EntityType.EMAIL,
        EntityType.SOCIAL_POST,
        EntityType.SOCIAL_COMMENT,
        EntityType.KNOWLEDGE_NOTE,
        EntityType.DOCUMENT,
    }
    PERSIST_FIELDS: ClassVar[list[str]] = []  # Custom persist_result

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_MULTIMODAL_MODEL,
        device: str | None = None,
    ) -> None:
        """Initialize the multimodal text embedding processor.

        Args:
            model_name: SigLIP model name.
            device: Device to run model on.
        """
        self._model_name = model_name
        self._models = MLModels(device=device)

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

    def execute(self, entity: SQLModel) -> StageResult:
        """Generate multimodal text embedding for an entity.

        Args:
            entity: The entity to process.

        Returns:
            StageResult with the generated embedding.
        """
        start_time = time.monotonic()

        entity_type = self._get_entity_type(entity)
        entity_id: UUID | None = getattr(entity, "id", None)
        if entity_type is None or entity_id is None:
            return StageResult(
                item_id=entity_id or uuid4(),
                stage_name=self.NAME,
                status=StageStatus.SKIPPED,
                error_message="Unknown entity type or missing ID",
            )

        text = self._get_text_content(entity, entity_type)
        if not text:
            return StageResult(
                item_id=entity_id,
                stage_name=self.NAME,
                status=StageStatus.SKIPPED,
                error_message="No text content to embed",
            )

        try:
            embedding = self._models.encode_text_multimodal(text, self._model_name)
            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            return StageResult(
                item_id=entity_id,
                stage_name=self.NAME,
                status=StageStatus.COMPLETED,
                processing_time_ms=elapsed_ms,
                data={
                    "multimodal_embedding": embedding,
                    "embedding_dim": len(embedding),
                    "model_name": self._model_name,
                    "text_length": len(text),
                },
            )

        except Exception as e:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            logger.exception(f"Multimodal text embedding failed: {e}")
            return StageResult(
                item_id=entity_id,
                stage_name=self.NAME,
                status=StageStatus.FAILED,
                processing_time_ms=elapsed_ms,
                error_message=f"Multimodal text embedding failed: {e}",
            )

    def _get_entity_type(self, entity: SQLModel) -> EntityType | None:
        """Determine EntityType from entity instance."""
        if isinstance(entity, ChatMessage):
            return EntityType.CHAT_MESSAGE
        if isinstance(entity, KnowledgeNote):
            return EntityType.KNOWLEDGE_NOTE
        if isinstance(entity, Document):
            return EntityType.DOCUMENT
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
        """Persist multimodal embedding to the entity.

        Saves to the `multimodal_embedding` field on all supported text entity types.

        Args:
            session: Database session.
            entity_type: The entity type.
            entity_id: The entity ID.
            result: The StageResult from execute().

        Returns:
            Dict with task result summary.
        """
        embedding = result.data.get("multimodal_embedding")

        if embedding and result.status == StageStatus.COMPLETED:
            entity = _get_entity(session, entity_type, entity_id)
            if entity is None:
                logger.warning(
                    f"Cannot persist multimodal embedding: {entity_type.value} {entity_id} not found"
                )
            elif not hasattr(entity, "multimodal_embedding"):
                logger.warning(
                    f"Cannot persist multimodal embedding: {entity_type.value} lacks "
                    "'multimodal_embedding' field"
                )
            else:
                entity.multimodal_embedding = embedding
                session.add(entity)
                session.commit()
                logger.debug(f"Persisted multimodal embedding for {entity_type.value} {entity_id}")

        return {
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "status": result.status.value,
            "embedding_dim": result.data.get("embedding_dim"),
            "processing_time_ms": result.processing_time_ms,
        }


@ProcessorRegistry.register(priority=28)
class MediaEmbeddingProcessor(BaseProcessor):
    """Processor for generating media embeddings using SigLIP.

    Generates 768-dimensional visual embeddings for images that share
    the same vector space as multimodal text embeddings, enabling
    cross-modal search (text queries finding images).

    Also generates text embeddings for OCR text and captions if available.
    """

    NAME: ClassVar[str] = "media_embedding"
    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {EntityType.MEDIA}
    PERSIST_FIELDS: ClassVar[list[str]] = []  # Custom persist_result for MediaEmbedding

    def __init__(
        self,
        *,
        siglip_model_name: str = DEFAULT_MULTIMODAL_MODEL,
        text_model_name: str = DEFAULT_TEXT_EMBEDDING_MODEL,
        device: str | None = None,
        generate_siglip: bool = True,
        generate_ocr_embedding: bool = True,
        generate_caption_embedding: bool = True,
    ) -> None:
        """Initialize the media embedding processor.

        Args:
            siglip_model_name: HuggingFace SigLIP model name for visual embeddings.
            text_model_name: Text model for OCR/caption embeddings.
            device: Device to run models on.
            generate_siglip: Whether to generate SigLIP embeddings.
            generate_ocr_embedding: Whether to embed OCR text.
            generate_caption_embedding: Whether to embed captions.
        """
        self._siglip_model_name = siglip_model_name
        self._text_model_name = text_model_name
        self._generate_siglip = generate_siglip
        self._generate_ocr_embedding = generate_ocr_embedding
        self._generate_caption_embedding = generate_caption_embedding
        self._models = MLModels(device=device)

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
            embeddings: dict[str, dict[str, Any]] = {}

            # Generate SigLIP visual embedding
            if self._generate_siglip:
                img = Image.open(file_path).convert("RGB")
                siglip_embedding = self._models.encode_image(img, self._siglip_model_name)
                embeddings["siglip"] = {
                    "embedding": siglip_embedding,
                    "model_name": self._siglip_model_name,
                    "embedding_type": EmbeddingType.CLIP.value,  # Reuse CLIP type for visual embeddings
                }

            # Generate OCR text embedding (e5)
            if self._generate_ocr_embedding and media.ocr_text:
                model = self._models.get_text_encoder(self._text_model_name)
                ocr_text_with_prefix = f"passage: {media.ocr_text}"
                ocr_embedding = model.encode(
                    ocr_text_with_prefix,
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                )
                embeddings["ocr"] = {
                    "embedding": ocr_embedding.tolist(),
                    "model_name": self._text_model_name,
                    "embedding_type": EmbeddingType.OCR.value,
                }

            # Generate caption embedding (e5)
            if self._generate_caption_embedding and media.caption:
                model = self._models.get_text_encoder(self._text_model_name)
                caption_text_with_prefix = f"passage: {media.caption}"
                caption_embedding = model.encode(
                    caption_text_with_prefix,
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                )
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

    def execute_batch(self, entities: list[SQLModel]) -> BatchStageResult:
        """Process a batch of media items.

        Uses batched image processing for SigLIP embeddings.

        Args:
            entities: List of media entities to process.

        Returns:
            BatchStageResult with individual results.
        """
        media_items: list[Media] = entities  # type: ignore[assignment]
        results: list[StageResult] = []
        valid_media: list[tuple[Media, Image.Image]] = []
        start_time = time.monotonic()

        # Pre-process: load images and filter valid media
        for media in media_items:
            if not self.should_execute(media):
                results.append(
                    StageResult(
                        item_id=media.id,
                        stage_name=self.NAME,
                        status=StageStatus.SKIPPED,
                    )
                )
                continue

            file_path = Path(media.file_path)
            if not file_path.exists():
                results.append(
                    StageResult(
                        item_id=media.id,
                        stage_name=self.NAME,
                        status=StageStatus.FAILED,
                        error_message=f"File not found: {media.file_path}",
                    )
                )
                continue

            try:
                img = Image.open(file_path).convert("RGB")
                valid_media.append((media, img))
            except Exception as e:
                logger.warning(f"Failed to load image {media.file_path}: {e}")
                results.append(
                    StageResult(
                        item_id=media.id,
                        stage_name=self.NAME,
                        status=StageStatus.FAILED,
                        error_message=f"Failed to load image: {e}",
                    )
                )

        # Batch process valid media
        if valid_media and self._generate_siglip:
            try:
                import torch

                model, processor = self._models.get_multimodal_encoder(self._siglip_model_name)
                images = [img for _, img in valid_media]

                # Batch process with SigLIP
                inputs = processor(images=images, return_tensors="pt")
                inputs = {k: v.to(self._models.device) for k, v in inputs.items()}

                with torch.no_grad():
                    outputs = model.get_image_features(**inputs)  # type: ignore[operator]
                    # Normalize embeddings
                    outputs = torch.nn.functional.normalize(outputs, dim=-1)
                    embeddings_np = outputs.cpu().numpy()

                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                per_media_ms = elapsed_ms // len(valid_media) if valid_media else 0

                for i, (media, _) in enumerate(valid_media):
                    embeddings: dict[str, dict[str, Any]] = {}
                    siglip_embedding: list[float] = embeddings_np[i].tolist()
                    embeddings["siglip"] = {
                        "embedding": siglip_embedding,
                        "model_name": self._siglip_model_name,
                        "embedding_type": EmbeddingType.CLIP.value,
                    }

                    # Add OCR/caption embeddings individually (not batched)
                    if self._generate_ocr_embedding and media.ocr_text:
                        text_model = self._models.get_text_encoder(self._text_model_name)
                        ocr_text_with_prefix = f"passage: {media.ocr_text}"
                        ocr_emb = text_model.encode(
                            ocr_text_with_prefix,
                            convert_to_numpy=True,
                            normalize_embeddings=True,
                        )
                        embeddings["ocr"] = {
                            "embedding": ocr_emb.tolist(),
                            "model_name": self._text_model_name,
                            "embedding_type": EmbeddingType.OCR.value,
                        }

                    if self._generate_caption_embedding and media.caption:
                        text_model = self._models.get_text_encoder(self._text_model_name)
                        cap_text_with_prefix = f"passage: {media.caption}"
                        cap_emb = text_model.encode(
                            cap_text_with_prefix,
                            convert_to_numpy=True,
                            normalize_embeddings=True,
                        )
                        embeddings["caption"] = {
                            "embedding": cap_emb.tolist(),
                            "model_name": self._text_model_name,
                            "embedding_type": EmbeddingType.CAPTION.value,
                        }

                    results.append(
                        StageResult(
                            item_id=media.id,
                            stage_name=self.NAME,
                            status=StageStatus.COMPLETED,
                            processing_time_ms=per_media_ms,
                            data={
                                "embeddings": embeddings,
                                "embedding_count": len(embeddings),
                            },
                        )
                    )

            except RuntimeError as e:
                # Check for CUDA OOM - fall back to individual processing
                if "out of memory" in str(e).lower():
                    logger.warning(
                        f"Batch media embedding OOM with {len(valid_media)} items, "
                        "falling back to individual processing"
                    )
                    for media, _ in valid_media:
                        individual_result = self.execute(media)
                        results.append(individual_result)
                else:
                    raise
            except Exception as e:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                logger.exception(f"Batch media embedding failed: {e}")
                for media, _ in valid_media:
                    results.append(
                        StageResult(
                            item_id=media.id,
                            stage_name=self.NAME,
                            status=StageStatus.FAILED,
                            processing_time_ms=elapsed_ms,
                            error_message=f"Batch embedding failed: {e}",
                        )
                    )

        return BatchStageResult(
            stage_name=self.NAME,
            total=len(entities),
            completed=sum(1 for r in results if r.status == StageStatus.COMPLETED),
            failed=sum(1 for r in results if r.status == StageStatus.FAILED),
            skipped=sum(1 for r in results if r.status == StageStatus.SKIPPED),
            results=results,
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
def run_text_embedding_processor_batch(
    self: "Task[..., dict[str, Any]]",
    entity_type: str,
    entity_ids: list[str],
) -> dict[str, Any]:
    """Generate text embeddings for a batch of entities."""
    return run_batch_processor_task(
        self, EntityType(entity_type), entity_ids, TextEmbeddingProcessor
    )


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_multimodal_text_embedding_processor(
    self: "Task[..., dict[str, Any]]",
    entity_type: str,
    entity_id: str,
) -> dict[str, Any]:
    """Generate multimodal text embedding for an entity."""
    return run_processor_task(
        self, EntityType(entity_type), entity_id, MultimodalTextEmbeddingProcessor
    )


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
    """Generate media embeddings (SigLIP, OCR, caption) for a media item."""
    return run_processor_task(self, EntityType(entity_type), entity_id, MediaEmbeddingProcessor)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="process",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_media_embedding_processor_batch(
    self: "Task[..., dict[str, Any]]",
    entity_type: str,
    entity_ids: list[str],
) -> dict[str, Any]:
    """Generate media embeddings for a batch of media items."""
    return run_batch_processor_task(
        self, EntityType(entity_type), entity_ids, MediaEmbeddingProcessor
    )


# Register tasks with their processors
ProcessorRegistry.set_task(TextEmbeddingProcessor.NAME, run_text_embedding_processor)
ProcessorRegistry.set_task(
    MultimodalTextEmbeddingProcessor.NAME, run_multimodal_text_embedding_processor
)
ProcessorRegistry.set_task(MediaEmbeddingProcessor.NAME, run_media_embedding_processor)
