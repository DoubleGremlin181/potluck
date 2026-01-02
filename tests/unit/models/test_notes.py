"""Tests for KnowledgeNote model."""

from uuid import UUID

from potluck.models.notes import KnowledgeNote


class TestNotesModels:
    """Tests for KnowledgeNote model (Potluck-native notes)."""

    def test_knowledge_note_creation(self) -> None:
        """KnowledgeNote can be created with content."""
        note = KnowledgeNote(content="I went to school with Jack.")
        assert isinstance(note.id, UUID)
        assert note.content == "I went to school with Jack."
        assert note.created_by is None

    def test_knowledge_note_with_creator(self) -> None:
        """KnowledgeNote tracks who/what created it."""
        note = KnowledgeNote(
            content="Alice's favorite restaurant is Pizzeria Uno.",
            created_by="claude",
        )
        assert note.created_by == "claude"

    def test_knowledge_note_with_linked_entities(self) -> None:
        """KnowledgeNote can link to other entities."""
        note = KnowledgeNote(
            content="Meeting notes from today",
            linked_entities='[{"entity_type": "person", "entity_id": "abc123"}]',
        )
        assert note.linked_entities is not None

    def test_knowledge_note_content_hash(self) -> None:
        """KnowledgeNote can have content hash for deduplication."""
        note = KnowledgeNote(
            content="Unique insight",
            content_hash="sha256_hash_value",
        )
        assert note.content_hash == "sha256_hash_value"
