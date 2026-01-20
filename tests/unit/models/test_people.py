"""Tests for Person and PersonAlias models."""

from datetime import date
from uuid import UUID, uuid4

from potluck.models.base import SourceType
from potluck.models.people import AliasType, Person, PersonAlias


class TestPeopleModels:
    """Tests for Person, PersonAlias, and FaceEncoding models."""

    def test_person_creation(self) -> None:
        """Person can be created with display_name."""
        person = Person(display_name="John Doe")
        assert isinstance(person.id, UUID)
        assert person.display_name == "John Doe"
        assert person.is_self is False
        assert person.is_merged is False

    def test_person_optional_fields(self) -> None:
        """Person optional fields can be set."""
        person = Person(
            display_name="Jane Doe",
            date_of_birth=date(1990, 5, 15),
            is_self=True,
        )
        assert person.date_of_birth == date(1990, 5, 15)
        assert person.is_self is True

    def test_person_merged_property(self) -> None:
        """is_merged property returns correct value."""
        person = Person(display_name="Original")
        assert person.is_merged is False

        person.merged_into_id = uuid4()
        assert person.is_merged is True

    def test_person_alias_creation(self) -> None:
        """PersonAlias can be created with required fields."""
        person_id = uuid4()
        alias = PersonAlias(
            person_id=person_id,
            alias_type=AliasType.EMAIL,
            value="john@example.com",
            source_type=SourceType.GOOGLE_TAKEOUT,
        )
        assert alias.person_id == person_id
        assert alias.alias_type == AliasType.EMAIL
        assert alias.value == "john@example.com"
        assert alias.confidence == 1.0
        assert alias.is_primary is False

    def test_alias_type_enum(self) -> None:
        """AliasType enum has expected values."""
        expected = {"name", "email", "phone", "username", "social_handle"}
        actual = {t.value for t in AliasType}
        assert actual == expected

    def test_person_alias_normalized_value(self) -> None:
        """PersonAlias can have normalized value for matching."""
        alias = PersonAlias(
            person_id=uuid4(),
            alias_type=AliasType.EMAIL,
            value="John.Doe@Example.COM",
            normalized_value="john.doe@example.com",
            source_type=SourceType.MANUAL,
        )
        assert alias.normalized_value == "john.doe@example.com"
