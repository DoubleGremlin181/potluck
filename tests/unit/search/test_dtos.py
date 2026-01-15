"""Tests for search DTOs."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from potluck.models.base import EntityType, SourceType
from potluck.search.dtos import (
    RankingConfig,
    RetrievalResult,
    SearchMode,
    SearchQuery,
    SearchResultItem,
    SearchResults,
)


class TestSearchMode:
    """Tests for SearchMode enum."""

    def test_all_modes_defined(self) -> None:
        """All expected search modes are defined."""
        expected = {"fts", "vector_text", "vector_multimodal", "hybrid"}
        actual = {m.value for m in SearchMode}
        assert actual == expected

    def test_mode_is_string(self) -> None:
        """SearchMode values are strings."""
        assert SearchMode.FTS.value == "fts"
        assert SearchMode.HYBRID.value == "hybrid"


class TestSearchQuery:
    """Tests for SearchQuery DTO."""

    def test_minimal_query(self) -> None:
        """Query with just the required query string."""
        q = SearchQuery(query="test search")
        assert q.query == "test search"
        assert q.mode == SearchMode.HYBRID
        assert q.limit == 20
        assert q.offset == 0
        assert q.entity_types is None
        assert q.since is None
        assert q.until is None

    def test_query_validation_min_length(self) -> None:
        """Query string must be non-empty."""
        with pytest.raises(ValidationError):
            SearchQuery(query="")

    def test_limit_validation(self) -> None:
        """Limit must be between 1 and 100."""
        # Valid limits
        SearchQuery(query="test", limit=1)
        SearchQuery(query="test", limit=100)

        # Invalid limits
        with pytest.raises(ValidationError):
            SearchQuery(query="test", limit=0)
        with pytest.raises(ValidationError):
            SearchQuery(query="test", limit=101)

    def test_offset_validation(self) -> None:
        """Offset must be non-negative."""
        SearchQuery(query="test", offset=0)
        SearchQuery(query="test", offset=100)

        with pytest.raises(ValidationError):
            SearchQuery(query="test", offset=-1)

    def test_entity_types_filter(self) -> None:
        """Entity types can be filtered."""
        q = SearchQuery(
            query="test",
            entity_types={EntityType.MEDIA, EntityType.CHAT_MESSAGE},
        )
        assert q.entity_types is not None
        assert EntityType.MEDIA in q.entity_types
        assert EntityType.CHAT_MESSAGE in q.entity_types
        assert len(q.entity_types) == 2

    def test_date_filters(self) -> None:
        """Date filters work correctly."""
        since = datetime(2024, 1, 1, tzinfo=UTC)
        until = datetime(2024, 12, 31, tzinfo=UTC)

        q = SearchQuery(query="test", since=since, until=until)
        assert q.since == since
        assert q.until == until

    def test_mode_override(self) -> None:
        """Search mode can be changed from default."""
        q = SearchQuery(query="test", mode=SearchMode.FTS)
        assert q.mode == SearchMode.FTS


class TestRankingConfig:
    """Tests for RankingConfig DTO."""

    def test_defaults(self) -> None:
        """Default ranking configuration."""
        config = RankingConfig()
        assert config.fts_weight == 0.3
        assert config.vector_weight == 0.7
        assert config.rrf_k == 60

    def test_weight_validation(self) -> None:
        """Weights must be between 0 and 1."""
        # Valid weights
        RankingConfig(fts_weight=0.0)
        RankingConfig(fts_weight=1.0)
        RankingConfig(vector_weight=0.0)
        RankingConfig(vector_weight=1.0)

        # Invalid weights
        with pytest.raises(ValidationError):
            RankingConfig(fts_weight=-0.1)
        with pytest.raises(ValidationError):
            RankingConfig(fts_weight=1.1)

    def test_rrf_k_validation(self) -> None:
        """RRF k must be positive."""
        RankingConfig(rrf_k=1)
        RankingConfig(rrf_k=100)

        with pytest.raises(ValidationError):
            RankingConfig(rrf_k=0)


class TestRetrievalResult:
    """Tests for RetrievalResult DTO."""

    def test_minimal_result(self) -> None:
        """Minimal retrieval result."""
        result = RetrievalResult(
            entity_type=EntityType.EMAIL,
            entity_id=uuid4(),
            score=0.85,
            rank=1,
        )
        assert result.entity_type == EntityType.EMAIL
        assert result.score == 0.85
        assert result.rank == 1
        assert result.snippet is None

    def test_with_snippet(self) -> None:
        """Retrieval result with snippet."""
        result = RetrievalResult(
            entity_type=EntityType.EMAIL,
            entity_id=uuid4(),
            score=0.85,
            rank=1,
            snippet="...matching <<text>> here...",
        )
        assert result.snippet == "...matching <<text>> here..."

    def test_rank_validation(self) -> None:
        """Rank must be at least 1 (1-indexed)."""
        RetrievalResult(
            entity_type=EntityType.EMAIL,
            entity_id=uuid4(),
            score=0.5,
            rank=1,
        )

        with pytest.raises(ValidationError):
            RetrievalResult(
                entity_type=EntityType.EMAIL,
                entity_id=uuid4(),
                score=0.5,
                rank=0,
            )


class TestSearchResultItem:
    """Tests for SearchResultItem DTO."""

    def test_minimal_result(self) -> None:
        """Minimal search result item."""
        item = SearchResultItem(
            entity_type=EntityType.CHAT_MESSAGE,
            entity_id=uuid4(),
            score=0.92,
        )
        assert item.entity_type == EntityType.CHAT_MESSAGE
        assert item.score == 0.92
        assert item.fts_rank is None
        assert item.vector_rank is None
        assert item.title is None
        assert item.snippet is None

    def test_full_result(self) -> None:
        """Search result with all fields."""
        now = datetime.now(UTC)
        entity_id = uuid4()

        item = SearchResultItem(
            entity_type=EntityType.EMAIL,
            entity_id=entity_id,
            score=0.88,
            fts_rank=3,
            vector_rank=5,
            title="Meeting Notes",
            snippet="...discussed the <<project>>...",
            occurred_at=now,
            source_type=SourceType.GOOGLE_TAKEOUT,
        )

        assert item.entity_id == entity_id
        assert item.fts_rank == 3
        assert item.vector_rank == 5
        assert item.title == "Meeting Notes"
        assert item.occurred_at == now
        assert item.source_type == SourceType.GOOGLE_TAKEOUT


class TestSearchResults:
    """Tests for SearchResults DTO."""

    def test_empty_results(self) -> None:
        """Empty search results."""
        results = SearchResults(
            query="nonexistent",
            mode=SearchMode.HYBRID,
            entity_types_searched=[EntityType.EMAIL, EntityType.MEDIA],
            total_count=0,
            items=[],
            took_ms=15,
        )
        assert results.query == "nonexistent"
        assert results.total_count == 0
        assert len(results.items) == 0
        assert results.took_ms == 15

    def test_with_results(self) -> None:
        """Search results with items."""
        items = [
            SearchResultItem(
                entity_type=EntityType.EMAIL,
                entity_id=uuid4(),
                score=0.95,
            ),
            SearchResultItem(
                entity_type=EntityType.CHAT_MESSAGE,
                entity_id=uuid4(),
                score=0.87,
            ),
        ]

        results = SearchResults(
            query="test query",
            mode=SearchMode.FTS,
            entity_types_searched=[EntityType.EMAIL, EntityType.CHAT_MESSAGE],
            total_count=2,
            items=items,
            took_ms=42,
        )

        assert results.total_count == 2
        assert len(results.items) == 2
        assert results.items[0].score == 0.95
