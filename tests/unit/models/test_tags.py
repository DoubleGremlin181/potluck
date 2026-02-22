"""Tests for Tag and TagAssignment models."""

from uuid import UUID, uuid4

from potluck.models.base import EntityType
from potluck.models.tags import Tag, TagAssignment


class TestTagModels:
    """Tests for Tag and TagAssignment models."""

    def test_tag_creation(self) -> None:
        """Tag can be created with name."""
        tag = Tag(name="python")
        assert isinstance(tag.id, UUID)
        assert tag.name == "python"
        assert tag.category is None
        assert tag.description is None

    def test_tag_with_category(self) -> None:
        """Tag can have a category for grouping."""
        tag = Tag(
            name="cafe",
            category="location",
            description="Good places to work from",
        )
        assert tag.name == "cafe"
        assert tag.category == "location"
        assert tag.description == "Good places to work from"

    def test_lambda_tag_creation(self) -> None:
        """Tag can be created without name (lambda/unnamed tag)."""
        tag = Tag(
            description="Quick note about this entity",
        )
        assert tag.name is None
        assert tag.description == "Quick note about this entity"

    def test_tag_assignment_creation(self) -> None:
        """TagAssignment can be created."""
        assignment = TagAssignment(
            tag_id=uuid4(),
            entity_type=EntityType.MEDIA,
            entity_id=uuid4(),
        )
        assert isinstance(assignment.id, UUID)
        assert assignment.entity_type == EntityType.MEDIA
