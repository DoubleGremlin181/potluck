"""Tests for ChatThread and ChatMessage models."""

from uuid import UUID, uuid4

from potluck.models.base import SourceType
from potluck.models.messages import (
    ChatMessage,
    ChatThread,
    ChatThreadParticipant,
    MessageType,
    ThreadType,
)


class TestMessageModels:
    """Tests for ChatThread and ChatMessage models."""

    def test_chat_thread_creation(self) -> None:
        """ChatThread can be created."""
        thread = ChatThread(source_type="whatsapp")
        assert isinstance(thread.id, UUID)
        assert thread.source_type == "whatsapp"
        assert thread.thread_type == ThreadType.DIRECT
        assert thread.message_count == 0
        assert thread.is_archived is False

    def test_thread_type_enum(self) -> None:
        """ThreadType enum has expected values."""
        expected = {"direct", "group", "channel", "community"}
        actual = {t.value for t in ThreadType}
        assert actual == expected

    def test_chat_message_creation(self) -> None:
        """ChatMessage can be created."""
        thread_id = uuid4()
        message = ChatMessage(
            source_type=SourceType.WHATSAPP,
            thread_id=thread_id,
            content="Hello!",
        )
        assert message.thread_id == thread_id
        assert message.content == "Hello!"
        assert message.message_type == MessageType.TEXT
        assert message.is_from_me is False

    def test_message_type_enum(self) -> None:
        """MessageType enum has expected values."""
        expected = {
            "text",
            "image",
            "video",
            "audio",
            "document",
            "sticker",
            "location",
            "contact",
            "poll",
            "system",
            "deleted",
            "other",
        }
        actual = {t.value for t in MessageType}
        assert actual == expected

    def test_chat_thread_participant_creation(self) -> None:
        """ChatThreadParticipant can be created."""
        participant = ChatThreadParticipant(
            thread_id=uuid4(),
            person_id=uuid4(),
            role="admin",
        )
        assert participant.role == "admin"
        assert participant.is_active is True
