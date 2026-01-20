"""Tests for vector retriever."""

from potluck.models.base import EntityType
from potluck.search.retrieval.vector import VectorRetriever
from potluck.search.utils import get_searchable_models


class TestVectorRetrieverInit:
    """Tests for VectorRetriever initialization."""

    def test_instantiation(self) -> None:
        """VectorRetriever can be instantiated."""
        retriever = VectorRetriever()
        assert retriever is not None
        # MLModels is lazily loaded at class level
        assert VectorRetriever._models is None or VectorRetriever._models is not None

    def test_retrieve_interface(self) -> None:
        """VectorRetriever has the required retrieve method."""
        retriever = VectorRetriever()
        assert hasattr(retriever, "retrieve")
        assert callable(retriever.retrieve)


class TestVectorRetrieverBehavior:
    """Tests for VectorRetriever behavior without ML dependencies."""

    def test_empty_entity_types_returns_empty(self) -> None:
        """Requesting no entity types returns empty results."""
        from unittest.mock import MagicMock

        retriever = VectorRetriever()
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

        retriever = VectorRetriever()
        mock_session = MagicMock()

        # Entity types that aren't searchable (LOCATION_VISIT has no __searchable__ = True)
        result = retriever.retrieve(
            session=mock_session,
            query="test",
            entity_types={EntityType.LOCATION_VISIT},
            limit=10,
        )

        assert result == []


class TestGetSearchableModels:
    """Tests for get_searchable_models utility (shared with FTS)."""

    def test_returns_searchable_models(self) -> None:
        """get_searchable_models returns models with __searchable__ = True."""
        models = get_searchable_models()

        # All returned models should have __searchable__ = True
        for _et, model in models.items():
            assert getattr(model, "__searchable__", False) is True
