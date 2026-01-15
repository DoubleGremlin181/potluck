"""Tests for vector retriever."""

from potluck.models.base import EntityType
from potluck.search.retrieval.vector import VectorRetriever, get_searchable_models


class TestVectorRetrieverInit:
    """Tests for VectorRetriever initialization."""

    def test_instantiation_without_models(self) -> None:
        """VectorRetriever can be instantiated without MLModels."""
        retriever = VectorRetriever()
        assert retriever is not None
        assert retriever._models is None

    def test_instantiation_with_models(self) -> None:
        """VectorRetriever accepts MLModels instance."""
        from unittest.mock import MagicMock

        mock_models = MagicMock()
        retriever = VectorRetriever(models=mock_models)
        assert retriever._models is mock_models

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

        # Entity types that aren't searchable
        result = retriever.retrieve(
            session=mock_session,
            query="test",
            entity_types={EntityType.PERSON, EntityType.TRANSACTION},
            limit=10,
        )

        assert result == []


class TestGetSearchableModels:
    """Tests for get_searchable_models utility (shared with FTS)."""

    def test_same_as_fts_searchable(self) -> None:
        """Vector and FTS use the same searchable model set."""
        from potluck.search.retrieval.fts import (
            get_searchable_models as fts_searchable,
        )

        fts_models = fts_searchable()
        vector_models = get_searchable_models()

        assert set(fts_models.keys()) == set(vector_models.keys())
