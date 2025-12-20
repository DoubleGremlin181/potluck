"""Tag and tagging models for entity organization."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from potluck.models.base import _utc_now
from potluck.models.links import EntityType


class Tag(SQLModel, table=True):
    """User-defined tag for organizing entities.

    Tags are user-created labels that can be applied to any entity type.
    """

    __tablename__ = "tags"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the tag",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the tag was created",
    )
    updated_at: datetime = Field(
        default_factory=_utc_now,
        sa_column_kwargs={"onupdate": _utc_now},
        description="When the tag was last updated",
    )

    # Tag information
    name: str = Field(
        index=True,
        unique=True,
        description="Tag name (lowercase, no spaces)",
    )
    display_name: str | None = Field(
        default=None,
        description="Display name with original casing",
    )
    description: str | None = Field(
        default=None,
        description="Description of what this tag means",
    )

    # Visual styling
    color: str | None = Field(
        default=None,
        description="Hex color code for the tag (e.g., '#FF5733')",
    )
    icon: str | None = Field(
        default=None,
        description="Icon name or emoji for the tag",
    )

    # Hierarchy
    parent_id: UUID | None = Field(
        default=None,
        foreign_key="tags.id",
        description="Parent tag for hierarchical organization",
    )
    full_path: str | None = Field(
        default=None,
        index=True,
        description="Full path from root (e.g., 'work/projects/potluck')",
    )

    # Usage statistics
    usage_count: int = Field(
        default=0,
        description="Number of entities with this tag",
    )

    # Status
    is_system: bool = Field(
        default=False,
        description="Whether this is a system-generated tag",
    )
    is_hidden: bool = Field(
        default=False,
        description="Whether to hide this tag in UI",
    )

    # Relationships
    assignments: list["TagAssignment"] = Relationship(back_populates="tag")


class TagAssignment(SQLModel, table=True):
    """Assignment of a tag to an entity.

    Links tags to any type of entity in the system.
    """

    __tablename__ = "tag_assignments"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the assignment",
    )
    tag_id: UUID = Field(
        foreign_key="tags.id",
        index=True,
        description="The tag being assigned",
    )
    entity_type: EntityType = Field(
        description="Type of the entity being tagged",
    )
    entity_id: UUID = Field(
        index=True,
        description="ID of the entity being tagged",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the tag was assigned",
    )

    # Assignment metadata
    is_automatic: bool = Field(
        default=False,
        description="Whether this tag was automatically assigned",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence for automatic assignments",
    )
    assigned_by: str | None = Field(
        default=None,
        description="What assigned this tag (user, classifier name, etc.)",
    )

    # Relationships
    tag: Tag = Relationship(back_populates="assignments")


class TagSynonym(SQLModel, table=True):
    """Synonym mapping for tags.

    Allows multiple names to map to the same canonical tag.
    """

    __tablename__ = "tag_synonyms"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier",
    )
    tag_id: UUID = Field(
        foreign_key="tags.id",
        index=True,
        description="The canonical tag",
    )
    synonym: str = Field(
        index=True,
        unique=True,
        description="The synonym (e.g., 'js' maps to 'javascript')",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the synonym was created",
    )
