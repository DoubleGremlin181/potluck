"""Search DTOs."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

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
    # List caps keep the rendered SQL well under SQLite's host-parameter
    # limit (one ? per entry) — oversized lists are a validation error, not
    # an sqlite3.OperationalError.
    kinds: list[ItemKind] | None = Field(default=None, max_length=16)
    sources: list[str] | None = Field(default=None, max_length=64)
    from_addrs: list[str] | None = Field(default=None, max_length=64)
    # Naive datetimes are read as UTC (matching the inline after:/before:
    # operators, which pin UTC midnight).
    after: datetime | None = None
    before: datetime | None = None
    prefix: bool = Field(
        default=False,
        description="Search-as-you-type: the last query token matches as a prefix.",
    )
    cursor: str | None = Field(
        default=None,
        description=(
            "Opaque pagination cursor from a previous response's next_cursor. "
            "Valid only for the exact query/prefix/filter set that produced it "
            "(anything else raises InvalidCursorError). Mutually exclusive with offset."
        ),
    )
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _cursor_xor_offset(self) -> "SearchRequest":
        if self.cursor is not None and self.offset != 0:
            raise ValueError("cursor and offset are mutually exclusive")
        return self


class SearchHit(BaseModel):
    """A single result from a full-text search."""

    id: int
    kind: ItemKind
    title: str | None
    title_highlight: str | None = Field(
        default=None,
        description=(
            "Title with [match] brackets around matched terms; None for "
            "filter-only (no free text) searches."
        ),
    )
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
    """Response from a full-text search.

    next_cursor: pass back as SearchRequest.cursor for the next page; None
    when the results are exhausted (or for filter-only searches, which page
    by offset). Cursors freeze the result set at the first page, so items
    ingested mid-pagination appear only in a fresh search.
    """

    query: str
    hits: list[SearchHit]
    next_cursor: str | None = None
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Inline operator values that were ignored, with reasons (e.g. an "
            "unknown kind: or a malformed date) — the search ran without them."
        ),
    )
