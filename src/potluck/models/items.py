"""Item DTOs."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, JsonValue, field_validator


class ItemKind(StrEnum):
    """Canonical 12-kind vocabulary for all ingested items.

    v0 → v1 kind mapping (v0 had ~40 entity types; v1 collapses to 12 kinds):

    - Document, KnowledgeNote       → note
    - Email                         → email
    - ChatMessage                   → message
    - Photo, media                  → photo
    - generic files                 → file
    - CalendarEvent                 → event
    - Contact, Person               → contact
    - LocationVisit, place          → location
    - Transaction                   → transaction
    - saved link, Bookmark          → bookmark
    - SocialFollow, social posts    → post
    - browser history, usage        → activity
    """

    NOTE = "note"
    EMAIL = "email"
    MESSAGE = "message"
    PHOTO = "photo"
    FILE = "file"
    EVENT = "event"
    CONTACT = "contact"
    LOCATION = "location"
    TRANSACTION = "transaction"
    BOOKMARK = "bookmark"
    POST = "post"
    ACTIVITY = "activity"


class Item(BaseModel):
    """A fully-hydrated item row returned from the storage layer."""

    id: int
    source: str  # sources.name via JOIN — DTOs speak names, not FK ids
    import_id: int
    kind: ItemKind
    external_id: str | None
    content_hash: str
    ts: datetime | None
    title: str | None
    text: str | None
    lat: float | None
    lon: float | None
    parent_id: int | None
    meta: dict[str, JsonValue]


class ItemSort(StrEnum):
    """Sort orders for item listing; undated items always sort last on ts."""

    TS_DESC = "ts_desc"
    TS_ASC = "ts_asc"
    ID_DESC = "id_desc"
    ID_ASC = "id_asc"


class ListItemsRequest(BaseModel):
    """Parameters for browsing items without a search query."""

    kinds: list[ItemKind] | None = None
    sources: list[str] | None = None  # sources.name values; unknown names match nothing
    since: datetime | None = Field(default=None, description="Inclusive lower bound on ts.")
    until: datetime | None = Field(default=None, description="Exclusive upper bound on ts.")
    sort: ItemSort = ItemSort.TS_DESC
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("since", "until")
    @classmethod
    def _naive_means_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class ItemSummary(BaseModel):
    """One row of a listing — full text and meta come from get_item."""

    id: int
    source: str
    kind: ItemKind
    ts: datetime | None
    title: str | None
    text_preview: str | None  # first 200 chars of text


class ListItemsResponse(BaseModel):
    """Response from an item listing."""

    items: list[ItemSummary]
    total: int  # COUNT(*) under the same filters, independent of pagination
    limit: int
    offset: int
