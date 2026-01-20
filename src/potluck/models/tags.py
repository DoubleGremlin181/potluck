"""Tag and tagging models for entity organization."""

from typing import ClassVar
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import Field, Relationship

from potluck.core.constants import MULTIMODAL_EMBEDDING_DIM, TEXT_EMBEDDING_DIM
from potluck.models.base import SimpleEntity
from potluck.models.links import EntityType


class Tag(SimpleEntity, table=True):
    """User-defined tag for organizing entities.

    Tags are labels that can be applied to any entity type.
    A tag with name=None is a "lambda tag" - just a quick note/annotation.

    Inherits id, created_at, updated_at from SimpleEntity.
    """

    __tablename__ = "tags"

    # Search configuration - name is priority, description auto-discovered
    __searchable__: ClassVar[bool] = True
    __search_exclude_fields__: ClassVar[set[str]] = set()
    __search_priority_fields__: ClassVar[set[str]] = {"name"}
    __search_date_fields__: ClassVar[set[str]] = {"created_at"}

    def to_search_repr(self) -> str:
        """Generate search result representation."""
        if self.name:
            return f"[Tag] {self.name}" + (f" ({self.category})" if self.category else "")
        return f"[Lambda Tag] {(self.description or '')[:60]}..."

    # Tag information
    name: str | None = Field(
        default=None,
        index=True,
        description="Tag name (None for lambda/unnamed tags)",
    )
    category: str | None = Field(
        default=None,
        index=True,
        description="Category grouping for the tag (e.g., 'location', 'topic', 'project')",
    )
    description: str | None = Field(
        default=None,
        description="Description or note content (especially for lambda tags)",
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

    # Relationships
    assignments: list["TagAssignment"] = Relationship(back_populates="tag")


class TagAssignment(SimpleEntity, table=True):
    """Assignment of a tag to an entity.

    Links tags to any type of entity in the system.
    Supports efficient lookup both ways:
    - Find all tags for an entity
    - Find all entities with a tag

    Inherits id, created_at, updated_at from SimpleEntity.
    """

    __tablename__ = "tag_assignments"

    tag_id: UUID = Field(
        foreign_key="tags.id",
        index=True,
        description="The tag being assigned",
    )
    entity_type: EntityType = Field(
        index=True,
        description="Type of the entity being tagged",
    )
    entity_id: UUID = Field(
        index=True,
        description="ID of the entity being tagged",
    )

    # Relationships
    tag: Tag = Relationship(back_populates="assignments")
