"""Tag and tagging models for entity organization."""

from uuid import UUID

from sqlmodel import Field, Relationship

from potluck.models.base import SimpleEntity
from potluck.models.links import EntityType


class Tag(SimpleEntity, table=True):
    """User-defined tag for organizing entities.

    Tags are labels that can be applied to any entity type.
    A tag with name=None is a "lambda tag" - just a quick note/annotation.

    Inherits id, created_at, updated_at from SimpleEntity.
    """

    __tablename__ = "tags"

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
