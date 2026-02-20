"""Tests for main search API."""

from unittest.mock import patch
from uuid import uuid4

from potluck.models.base import EntityType
from potluck.search import (
    SearchMode,
    SearchQuery,
    get_searchable_entity_types,
)
from potluck.search.dtos import RankingConfig, RetrievalResult


class TestGetSearchableEntityTypes:
    """Tests for get_searchable_entity_types utility."""

    def test_returns_set(self) -> None:
        """Returns a set of entity types."""
        result = get_searchable_entity_types()
        assert isinstance(result, set)

    def test_expected_searchable_types(self) -> None:
        """Returns expected searchable entity types."""
        result = get_searchable_entity_types()

        # These should be searchable
        expected = {
            EntityType.CHAT_MESSAGE,
            EntityType.EMAIL,
            EntityType.SOCIAL_POST,
            EntityType.SOCIAL_COMMENT,
            EntityType.KNOWLEDGE_NOTE,
            EntityType.DOCUMENT,
            EntityType.MEDIA,
            EntityType.CALENDAR_EVENT,
            EntityType.BROWSING_HISTORY,
            EntityType.BOOKMARK,
            EntityType.TRANSACTION,
            EntityType.PERSON,
            EntityType.LOCATION,
            EntityType.TAG,
        }

        assert result == expected

    def test_excludes_non_searchable(self) -> None:
        """Excludes non-searchable entity types."""
        result = get_searchable_entity_types()

        # These should NOT be searchable
        assert EntityType.LOCATION_VISIT not in result


class TestSearchQuery:
    """Tests for SearchQuery construction."""

    def test_hybrid_is_default_mode(self) -> None:
        """HYBRID is the default search mode."""
        query = SearchQuery(query="test")
        assert query.mode == SearchMode.HYBRID

    def test_all_entity_types_when_none(self) -> None:
        """None entity_types means search all."""
        query = SearchQuery(query="test")
        assert query.entity_types is None

    def test_entity_types_filter(self) -> None:
        """Entity types can be filtered."""
        query = SearchQuery(
            query="test",
            entity_types={EntityType.EMAIL, EntityType.MEDIA},
        )
        assert query.entity_types is not None
        assert EntityType.EMAIL in query.entity_types
        assert EntityType.MEDIA in query.entity_types
        assert EntityType.CHAT_MESSAGE not in query.entity_types


class TestSearchModes:
    """Tests for SearchMode enum."""

    def test_fts_mode(self) -> None:
        """FTS mode for keyword-only search."""
        query = SearchQuery(query="test", mode=SearchMode.FTS)
        assert query.mode == SearchMode.FTS

    def test_vector_text_mode(self) -> None:
        """Vector text mode for semantic search."""
        query = SearchQuery(query="test", mode=SearchMode.VECTOR_TEXT)
        assert query.mode == SearchMode.VECTOR_TEXT

    def test_vector_multimodal_mode(self) -> None:
        """Vector multimodal mode for cross-modal search."""
        query = SearchQuery(query="sunset", mode=SearchMode.VECTOR_MULTIMODAL)
        assert query.mode == SearchMode.VECTOR_MULTIMODAL

    def test_hybrid_mode(self) -> None:
        """Hybrid mode combines FTS and vector."""
        query = SearchQuery(query="test", mode=SearchMode.HYBRID)
        assert query.mode == SearchMode.HYBRID


class TestHybridSearchFallback:
    """Tests for graceful degradation when a retriever fails in hybrid mode."""

    async def test_vector_failure_falls_back_to_fts(self) -> None:
        """When vector retriever fails in hybrid mode, search should return FTS results."""
        from potluck.search import _run_retrievers_parallel

        fts_results = [
            RetrievalResult(
                entity_id=uuid4(),
                entity_type=EntityType.EMAIL,
                score=0.9,
                rank=1,
            )
        ]

        query = SearchQuery(query="test", mode=SearchMode.HYBRID)
        target_types = {EntityType.EMAIL}
        config = RankingConfig()

        with (
            patch("potluck.search._run_fts_retriever", return_value=fts_results),
            patch(
                "potluck.search._run_vector_retriever",
                side_effect=ValueError("expected ndim to be 1"),
            ),
        ):
            from unittest.mock import AsyncMock

            mock_session = AsyncMock()
            result = await _run_retrievers_parallel(mock_session, query, target_types, 60, config)

        assert "fts" in result
        assert len(result["fts"]) == 1
        assert "vector" not in result

    async def test_fts_only_mode_still_raises(self) -> None:
        """In FTS-only mode, FTS failure should re-raise."""
        from potluck.search import _run_retrievers_parallel

        query = SearchQuery(query="test", mode=SearchMode.FTS)
        target_types = {EntityType.EMAIL}
        config = RankingConfig()

        with patch(
            "potluck.search._run_fts_retriever",
            side_effect=ValueError("FTS error"),
        ):
            from unittest.mock import AsyncMock

            mock_session = AsyncMock()
            try:
                await _run_retrievers_parallel(mock_session, query, target_types, 60, config)
                raise AssertionError("Expected ValueError to be raised")
            except ValueError as e:
                assert "FTS error" in str(e)
