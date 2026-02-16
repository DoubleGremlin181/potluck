"""Knowledge notes model for Potluck.

KnowledgeNotes store useful information as searchable text. They can be:
- Created manually by users or LLMs within Potluck (source_type=MANUAL)
- Imported from external sources like Obsidian vaults or text files (source_type=TEXT_FILES)

Think of it as an LLM memory function - capturing insights, facts, and
relationships that don't have a hard tie to a source but can benefit
other models (e.g., "I went to school with Jack", "Alice's favorite
restaurant is Pizzeria Uno").
"""

from typing import ClassVar

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import Field

from potluck.core.constants import MULTIMODAL_EMBEDDING_DIM, TEXT_EMBEDDING_DIM
from potluck.models.base import BaseEntity, SourceType


class KnowledgeNote(BaseEntity, table=True):
    """A knowledge note with text and embedding, supporting both manual and imported notes.

    Extends BaseEntity to include source_type, source_id, and content_hash for
    tracking origin and deduplication. Defaults source_type to MANUAL so existing
    user/LLM-created notes continue to work unchanged.

    Inherits id, created_at, updated_at, source_type, source_id, content_hash
    from BaseEntity.
    """

    __tablename__ = "knowledge_notes"

    # Search configuration - content auto-discovered
    __searchable__: ClassVar[bool] = True
    __search_exclude_fields__: ClassVar[set[str]] = set()
    __search_priority_fields__: ClassVar[set[str]] = set()
    __search_date_fields__: ClassVar[set[str]] = {"created_at"}

    # Override source_type default so manual notes don't need to specify it
    source_type: SourceType = Field(
        default=SourceType.MANUAL,
        description="The source system this note was imported from (defaults to MANUAL)",
    )

    def to_text_repr(self) -> str:
        """Return text representation with ID for LLM context."""
        content_preview = (self.content or "")[:100]
        if len(self.content or "") > 100:
            content_preview += "..."
        creator = self.created_by or "unknown"
        parts = [f"Note (id: {self.id}): {content_preview} | by: {creator}"]
        if self.source_type != SourceType.MANUAL:
            parts.append(f"source: {self.source_type.value}")
            if self.source_id:
                parts.append(f"file: {self.source_id}")
        return " | ".join(parts)

    # Note content
    content: str = Field(
        description="The note text content",
    )

    # Embeddings for semantic search
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(TEXT_EMBEDDING_DIM)),
        description="Text embedding for text-to-text semantic search",
    )
    multimodal_embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(MULTIMODAL_EMBEDDING_DIM)),
        description="Multimodal embedding for text-to-image cross-modal search",
    )

    # Full-text search vector (auto-populated by database trigger)
    search_vector: str | None = Field(
        default=None,
        sa_column=Column(TSVECTOR),
        description="FTS vector for keyword search (auto-populated by trigger)",
    )

    # Creation context
    created_by: str | None = Field(
        default=None,
        description="What created this note (e.g., 'user', 'claude', 'auto-linker', 'import')",
    )

    # Optional linked entities (JSON-encoded UUIDs with entity types)
    linked_entities: str | None = Field(
        default=None,
        description="JSON-encoded list of {entity_type, entity_id} for related entities",
    )
