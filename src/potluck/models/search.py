"""Search DTOs."""

from datetime import datetime

from pydantic import BaseModel, Field

from potluck.models.items import ItemKind


class SearchRequest(BaseModel):
    """Parameters for a full-text search query.

    The query string may carry inline operators (``from:``, ``source:``,
    ``kind:``, ``before:``, ``after:`` — see search.query.parse_query); the
    structured fields below express the same filters programmatically and WIN
    over inline operators when both are present. ``after`` is inclusive,
    ``before`` exclusive.
    """

    query: str = Field(
        max_length=1000,
        description="Full-text search query (max 1000 chars; guards against pathological inputs).",
    )
    kinds: list[ItemKind] | None = None
    sources: list[str] | None = None
    from_addrs: list[str] | None = None
    after: datetime | None = None
    before: datetime | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class SearchHit(BaseModel):
    """A single result from a full-text search."""

    id: int
    kind: ItemKind
    title: str | None
    snippet: str
    score: float = Field(
        description=(
            "Raw BM25 score from FTS5. Always negative; more-negative means a better match. "
            "Results arrive pre-sorted ascending (best first). "
            "Adapter authors must not re-sort descending."
        )
    )
    ts: datetime | None


class SearchResponse(BaseModel):
    """Response from a full-text search."""

    query: str
    hits: list[SearchHit]
