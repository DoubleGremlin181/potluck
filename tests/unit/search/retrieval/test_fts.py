"""Tests for FTS retriever."""

from potluck.models.base import EntityType
from potluck.search.retrieval.fts import FTSRetriever
from potluck.search.utils import get_searchable_models


class TestGetSearchableModels:
    """Tests for get_searchable_models utility."""

    def test_returns_dict(self) -> None:
        """Returns a dictionary of entity types to models."""
        result = get_searchable_models()
        assert isinstance(result, dict)

    def test_only_searchable_entities(self) -> None:
        """Only returns entities with __searchable__ = True."""
        result = get_searchable_models()

        # These should be searchable based on model definitions
        searchable_types = {
            EntityType.CHAT_MESSAGE,
            EntityType.EMAIL,
            EntityType.SOCIAL_POST,
            EntityType.SOCIAL_COMMENT,
            EntityType.KNOWLEDGE_NOTE,
            EntityType.MEDIA,
            EntityType.CALENDAR_EVENT,
            EntityType.BROWSING_HISTORY,
            EntityType.BOOKMARK,
            EntityType.TRANSACTION,
            EntityType.PERSON,
            EntityType.LOCATION,
            EntityType.TAG,
        }

        for et in result:
            assert et in searchable_types

    def test_excludes_non_searchable(self) -> None:
        """Non-searchable entities are excluded."""
        result = get_searchable_models()

        # These should NOT be searchable
        non_searchable = {
            EntityType.LOCATION_VISIT,  # LocationVisit is not searchable
        }

        for et in non_searchable:
            assert et not in result

    def test_models_have_search_config(self) -> None:
        """All returned models have search configuration."""
        result = get_searchable_models()

        for _et, model in result.items():
            assert getattr(model, "__searchable__", False) is True
            # New search config uses auto-discovery with optional exclusions
            assert hasattr(model, "__search_exclude_fields__")
            assert hasattr(model, "__search_priority_fields__")
            assert hasattr(model, "__search_date_fields__")


class TestFTSRetriever:
    """Tests for FTSRetriever class."""

    def test_instantiation(self) -> None:
        """FTSRetriever can be instantiated."""
        retriever = FTSRetriever()
        assert retriever is not None

    def test_retrieve_interface(self) -> None:
        """FTSRetriever has the required retrieve method."""
        retriever = FTSRetriever()
        assert hasattr(retriever, "retrieve")
        assert callable(retriever.retrieve)

    def test_empty_entity_types_returns_empty(self) -> None:
        """Requesting no entity types returns empty results."""
        from unittest.mock import MagicMock

        retriever = FTSRetriever()
        mock_session = MagicMock()

        # Empty set of entity types
        result = retriever.retrieve(
            session=mock_session,
            query="test",
            entity_types=set(),
            limit=10,
        )

        assert result == []

    def test_non_searchable_entity_types_returns_empty(self) -> None:
        """Requesting non-searchable entity types returns empty results."""
        from unittest.mock import MagicMock

        retriever = FTSRetriever()
        mock_session = MagicMock()

        # Entity types that aren't searchable (LOCATION_VISIT has no __searchable__ = True)
        result = retriever.retrieve(
            session=mock_session,
            query="test",
            entity_types={EntityType.LOCATION_VISIT},
            limit=10,
        )

        assert result == []
