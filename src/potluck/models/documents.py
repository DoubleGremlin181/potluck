"""Document model for imported text content.

Documents represent text imported from external sources like:
- Google Keep notes
- Obsidian vaults
- Google Docs exports
- Generic text/markdown/HTML files

Unlike KnowledgeNote (which is Potluck-native), Documents always have
a source_type tracking their origin.
"""

from typing import ClassVar

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import Field

from potluck.core.constants import MULTIMODAL_EMBEDDING_DIM, TEXT_EMBEDDING_DIM
from potluck.models.base import BaseEntity


class Document(BaseEntity, table=True):
    """An imported document with text content and embeddings.

    For external text content imported from various sources.
    Inherits source_type, source_id, content_hash from BaseEntity.
    """

    __tablename__ = "documents"

    # Search configuration
    __searchable__: ClassVar[bool] = True
    __search_exclude_fields__: ClassVar[set[str]] = set()
    __search_priority_fields__: ClassVar[set[str]] = {"title"}
    __search_date_fields__: ClassVar[set[str]] = {"created_at"}

    def to_text_repr(self) -> str:
        """Return text representation with ID for LLM context."""
        title_str = self.title or "Untitled"
        content_preview = (self.content or "")[:100]
        if len(self.content or "") > 100:
            content_preview += "..."
        return (
            f"Document (id: {self.id}): {title_str} | "
            f"{content_preview} | source: {self.source_type.value}"
        )

    # Document content
    title: str | None = Field(
        default=None,
        description="Document title (from filename, front-matter, or source metadata)",
    )
    content: str = Field(
        description="The document text content",
    )
    file_extension: str | None = Field(
        default=None,
        description="Original file extension (e.g., '.md', '.txt', '.html')",
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
