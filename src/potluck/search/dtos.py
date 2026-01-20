"""Data transfer objects for search operations."""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from potluck.models.base import EntityType, SourceType


class SearchMode(str, Enum):
    """Type of search to perform."""

    FTS = "fts"  # Full-text search only
    VECTOR_TEXT = "vector_text"  # 384d text-to-text semantic search
    VECTOR_MULTIMODAL = "vector_multimodal"  # 768d cross-modal search
    HYBRID = "hybrid"  # FTS + vector combined (default)


class SearchQuery(BaseModel):
    """Input for search operations."""

    query: str = Field(min_length=1, description="Search query text")
    entity_types: set[EntityType] | None = Field(
        default=None,
        description="Entity types to search. None = all searchable types.",
    )
    mode: SearchMode = Field(
        default=SearchMode.HYBRID,
        description="Search mode to use",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of results to return",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of results to skip for pagination",
    )
    since: datetime | None = Field(
        default=None,
        description="Only return results after this datetime",
    )
    until: datetime | None = Field(
        default=None,
        description="Only return results before this datetime",
    )
    source_types: set[SourceType] | None = Field(
        default=None,
        description="Filter by source types",
    )


class RankingConfig(BaseModel):
    """Configuration for result ranking."""

    fts_weight: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Weight for FTS results in hybrid search",
    )
    vector_weight: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Weight for vector results in hybrid search",
    )
    rrf_k: int = Field(
        default=60,
        ge=1,
        description="RRF constant k that dampens high rankings",
    )


class RetrievalResult(BaseModel):
    """Single result from a retriever (internal use)."""

    entity_type: EntityType
    entity_id: UUID
    score: float = Field(description="Retriever-specific score (ts_rank or similarity)")
    rank: int = Field(ge=1, description="1-indexed position in retriever's result list")
    snippet: str | None = Field(
        default=None,
        description="Text snippet with search highlights (FTS only)",
    )


class SearchResultItem(BaseModel):
    """Single search result after ranking (public)."""

    entity_type: EntityType
    entity_id: UUID
    score: float = Field(description="Combined ranking score (0-1)")
    fts_rank: int | None = Field(
        default=None,
        description="Position in FTS results (1-indexed), None if not in FTS results",
    )
    vector_rank: int | None = Field(
        default=None,
        description="Position in vector results (1-indexed), None if not in vector results",
    )
    title: str | None = Field(
        default=None,
        description="Title or subject if available",
    )
    snippet: str | None = Field(
        default=None,
        description="Text snippet with search term highlights",
    )
    occurred_at: datetime | None = Field(
        default=None,
        description="When the entity occurred/was created",
    )
    source_type: SourceType | None = Field(
        default=None,
        description="Source of the entity",
    )


class SearchResults(BaseModel):
    """Complete search response."""

    query: str = Field(description="Original query string")
    mode: SearchMode = Field(description="Search mode used")
    entity_types_searched: list[EntityType] = Field(
        description="Entity types that were searched",
    )
    total_count: int = Field(description="Total number of matching results")
    items: list[SearchResultItem] = Field(description="Ranked search results")
    took_ms: int = Field(description="Search execution time in milliseconds")
