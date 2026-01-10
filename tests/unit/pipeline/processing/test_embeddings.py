"""Unit tests for embedding processors."""

from typing import cast

import pytest
from sqlmodel import SQLModel

# Skip entire module if ML dependencies not installed
pytest.importorskip("torch")
pytest.importorskip("sentence_transformers")

from uuid import uuid4

from potluck.core.constants import MULTIMODAL_EMBEDDING_DIM, TEXT_EMBEDDING_DIM
from potluck.models.base import EntityType
from potluck.models.media import Media, MediaType
from potluck.models.messages import ChatMessage
from potluck.models.notes import KnowledgeNote
from potluck.pipeline.dtos import StageStatus
from potluck.pipeline.processing.embeddings import (
    MediaEmbeddingProcessor,
    MultimodalTextEmbeddingProcessor,
    TextEmbeddingProcessor,
)


class TestTextEmbeddingProcessor:
    """Tests for TextEmbeddingProcessor."""

    def test_stage_has_name(self) -> None:
        """TextEmbeddingProcessor should have a NAME attribute."""
        processor = TextEmbeddingProcessor()
        assert processor.NAME == "text_embedding"

    def test_supported_entity_types(self) -> None:
        """TextEmbeddingProcessor should support text entity types."""
        processor = TextEmbeddingProcessor()
        assert EntityType.CHAT_MESSAGE in processor.SUPPORTED_ENTITY_TYPES
        assert EntityType.EMAIL in processor.SUPPORTED_ENTITY_TYPES
        assert EntityType.SOCIAL_POST in processor.SUPPORTED_ENTITY_TYPES
        assert EntityType.SOCIAL_COMMENT in processor.SUPPORTED_ENTITY_TYPES
        assert EntityType.KNOWLEDGE_NOTE in processor.SUPPORTED_ENTITY_TYPES
        # Should not support media
        assert EntityType.MEDIA not in processor.SUPPORTED_ENTITY_TYPES

    def test_skip_entity_without_text(self) -> None:
        """TextEmbeddingProcessor should skip entities with no text content."""
        processor = TextEmbeddingProcessor()
        message = ChatMessage(
            id=uuid4(),
            content="",  # Empty content
            source_type="test",
            thread_id=uuid4(),
        )

        result = processor.execute(message)

        assert result.status == StageStatus.SKIPPED
        assert result.error_message is not None
        assert "no text content" in result.error_message.lower()

    def test_embedding_dimension(self) -> None:
        """TextEmbeddingProcessor should produce correct dimension embeddings."""
        processor = TextEmbeddingProcessor(device="cpu")
        message = ChatMessage(
            id=uuid4(),
            content="This is a test message for embedding.",
            source_type="test",
            thread_id=uuid4(),
        )

        result = processor.execute(message)

        assert result.status == StageStatus.COMPLETED
        assert result.data.get("embedding_dim") == TEXT_EMBEDDING_DIM
        assert len(result.data.get("embedding", [])) == TEXT_EMBEDDING_DIM

    def test_get_entity_type_chat_message(self) -> None:
        """TextEmbeddingProcessor should identify ChatMessage entity type."""
        processor = TextEmbeddingProcessor()
        message = ChatMessage(
            id=uuid4(),
            content="test",
            source_type="test",
            thread_id=uuid4(),
        )

        entity_type = processor._get_entity_type(message)
        assert entity_type == EntityType.CHAT_MESSAGE

    def test_get_entity_type_knowledge_note(self) -> None:
        """TextEmbeddingProcessor should identify KnowledgeNote entity type."""
        processor = TextEmbeddingProcessor()
        note = KnowledgeNote(
            id=uuid4(),
            content="test note",
        )

        entity_type = processor._get_entity_type(note)
        assert entity_type == EntityType.KNOWLEDGE_NOTE


class TestMultimodalTextEmbeddingProcessor:
    """Tests for MultimodalTextEmbeddingProcessor."""

    def test_stage_has_name(self) -> None:
        """MultimodalTextEmbeddingProcessor should have a NAME attribute."""
        processor = MultimodalTextEmbeddingProcessor()
        assert processor.NAME == "multimodal_text_embedding"

    def test_supported_entity_types(self) -> None:
        """MultimodalTextEmbeddingProcessor should support text entity types."""
        processor = MultimodalTextEmbeddingProcessor()
        assert EntityType.CHAT_MESSAGE in processor.SUPPORTED_ENTITY_TYPES
        assert EntityType.KNOWLEDGE_NOTE in processor.SUPPORTED_ENTITY_TYPES

    def test_embedding_dimension(self) -> None:
        """MultimodalTextEmbeddingProcessor should produce correct dimension embeddings."""
        processor = MultimodalTextEmbeddingProcessor(device="cpu")
        message = ChatMessage(
            id=uuid4(),
            content="This is a test message for multimodal embedding.",
            source_type="test",
            thread_id=uuid4(),
        )

        result = processor.execute(message)

        assert result.status == StageStatus.COMPLETED
        assert result.data.get("embedding_dim") == MULTIMODAL_EMBEDDING_DIM
        assert len(result.data.get("multimodal_embedding", [])) == MULTIMODAL_EMBEDDING_DIM

    def test_skip_entity_without_text(self) -> None:
        """MultimodalTextEmbeddingProcessor should skip entities with no text content."""
        processor = MultimodalTextEmbeddingProcessor()
        message = ChatMessage(
            id=uuid4(),
            content="",
            source_type="test",
            thread_id=uuid4(),
        )

        result = processor.execute(message)

        assert result.status == StageStatus.SKIPPED


class TestMediaEmbeddingProcessor:
    """Tests for MediaEmbeddingProcessor."""

    def test_stage_has_name(self) -> None:
        """MediaEmbeddingProcessor should have a NAME attribute."""
        processor = MediaEmbeddingProcessor()
        assert processor.NAME == "media_embedding"

    def test_supported_entity_types(self) -> None:
        """MediaEmbeddingProcessor should only support media entities."""
        processor = MediaEmbeddingProcessor()
        assert EntityType.MEDIA in processor.SUPPORTED_ENTITY_TYPES
        assert len(processor.SUPPORTED_ENTITY_TYPES) == 1

    def test_should_execute_only_images(self) -> None:
        """MediaEmbeddingProcessor should only process images."""
        processor = MediaEmbeddingProcessor()

        image_media = Media(
            id=uuid4(),
            file_path="/test.jpg",
            media_type=MediaType.IMAGE,
            source_type="generic",
        )
        video_media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )

        assert processor.should_execute(image_media) is True
        assert processor.should_execute(video_media) is False

    def test_skip_non_image(self) -> None:
        """MediaEmbeddingProcessor should skip non-image media."""
        processor = MediaEmbeddingProcessor()
        media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )

        result = processor.execute(media)

        assert result.status == StageStatus.SKIPPED

    def test_missing_file_fails(self) -> None:
        """MediaEmbeddingProcessor should fail for missing files."""
        processor = MediaEmbeddingProcessor()
        media = Media(
            id=uuid4(),
            file_path="/nonexistent/file.jpg",
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result = processor.execute(media)

        assert result.status == StageStatus.FAILED
        assert result.error_message is not None
        assert "not found" in result.error_message.lower()


class TestTextEmbeddingBatch:
    """Tests for batch processing in TextEmbeddingProcessor."""

    def test_batch_processes_multiple_entities(self) -> None:
        """TextEmbeddingProcessor batch should process multiple entities."""
        processor = TextEmbeddingProcessor(device="cpu")
        messages = [
            ChatMessage(
                id=uuid4(),
                content=f"Test message {i}",
                source_type="test",
                thread_id=uuid4(),
            )
            for i in range(3)
        ]

        batch_result = processor.execute_batch(cast(list[SQLModel], messages))

        assert batch_result.total == 3
        assert batch_result.completed == 3
        assert batch_result.failed == 0
        assert batch_result.skipped == 0

    def test_batch_handles_mixed_results(self) -> None:
        """TextEmbeddingProcessor batch should handle mix of valid and empty."""
        processor = TextEmbeddingProcessor(device="cpu")
        messages = [
            ChatMessage(
                id=uuid4(),
                content="Valid message",
                source_type="test",
                thread_id=uuid4(),
            ),
            ChatMessage(
                id=uuid4(),
                content="",  # Empty - should be skipped
                source_type="test",
                thread_id=uuid4(),
            ),
            ChatMessage(
                id=uuid4(),
                content="Another valid message",
                source_type="test",
                thread_id=uuid4(),
            ),
        ]

        batch_result = processor.execute_batch(cast(list[SQLModel], messages))

        assert batch_result.total == 3
        assert batch_result.completed == 2
        assert batch_result.skipped == 1
        assert batch_result.failed == 0

    def test_batch_aggregates_correctly(self) -> None:
        """TextEmbeddingProcessor batch should aggregate results correctly."""
        processor = TextEmbeddingProcessor(device="cpu")
        messages = [
            ChatMessage(
                id=uuid4(),
                content="Test message",
                source_type="test",
                thread_id=uuid4(),
            )
        ]

        batch_result = processor.execute_batch(cast(list[SQLModel], messages))

        assert len(batch_result.results) == 1
        assert batch_result.results[0].status == StageStatus.COMPLETED
        assert batch_result.results[0].data.get("embedding_dim") == TEXT_EMBEDDING_DIM
