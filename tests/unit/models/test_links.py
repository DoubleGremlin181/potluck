"""Tests for EntityLink model."""

from uuid import uuid4

from potluck.models.links import EntityLink, EntityType, LinkType


class TestEntityLinkModels:
    """Tests for EntityLink model."""

    def test_entity_link_creation(self) -> None:
        """EntityLink can be created."""
        link = EntityLink(
            source_type=EntityType.MEDIA,
            source_id=uuid4(),
            target_type=EntityType.PERSON,
            target_id=uuid4(),
            link_type=LinkType.MENTIONS,
        )
        assert link.link_type == LinkType.MENTIONS
        assert link.confidence == 1.0
        assert link.is_automatic is True
        assert link.is_confirmed is False

    def test_link_type_enum(self) -> None:
        """LinkType enum has expected values."""
        expected = {
            "same_time",
            "before",
            "after",
            "during",
            "same_location",
            "near",
            "related",
            "similar",
            "references",
            "reply_to",
            "quote",
            "mentions",
            "about",
            "sent_by",
            "received_by",
            "custom",
        }
        actual = {t.value for t in LinkType}
        assert actual == expected

    def test_entity_type_enum(self) -> None:
        """EntityType enum has expected values."""
        expected = {
            "media",
            "chat_message",
            "email",
            "social_post",
            "social_comment",
            "knowledge_note",
            "calendar_event",
            "transaction",
            "budget",
            "subscription",
            "location",
            "location_visit",
            "browsing_history",
            "bookmark",
            "person",
            "tag",
            "document",
        }
        actual = {t.value for t in EntityType}
        assert actual == expected

    def test_entity_link_is_bidirectional(self) -> None:
        """is_bidirectional property returns correct value."""
        # Bidirectional links
        for link_type in [LinkType.SAME_TIME, LinkType.SAME_LOCATION, LinkType.RELATED]:
            link = EntityLink(
                source_type=EntityType.MEDIA,
                source_id=uuid4(),
                target_type=EntityType.MEDIA,
                target_id=uuid4(),
                link_type=link_type,
            )
            assert link.is_bidirectional is True

        # Directional links
        for link_type in [LinkType.BEFORE, LinkType.AFTER, LinkType.MENTIONS]:
            link = EntityLink(
                source_type=EntityType.MEDIA,
                source_id=uuid4(),
                target_type=EntityType.MEDIA,
                target_id=uuid4(),
                link_type=link_type,
            )
            assert link.is_bidirectional is False
