"""Item DTOs."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, JsonValue, field_validator


class ItemKind(StrEnum):
    """Canonical 12-kind vocabulary for all ingested items.

    Source data maps onto kinds as follows:

    - documents, knowledge notes    → note
    - emails                        → email
    - chat messages                 → message
    - photos, media                 → photo
    - generic files                 → file
    - calendar events               → event
    - contacts, people              → contact
    - location visits, places       → location
    - transactions                  → transaction
    - saved links                   → bookmark
    - social posts, follows         → post
    - browser history, app usage    → activity
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


class AttachmentDetail(BaseModel):
    """One files-table row on an item detail (#200)."""

    filename: str  # member_path; unnamed MIME parts surface as "part-N"
    mime: str | None
    size_bytes: int | None
    sha256: str | None


class EmailDetail(BaseModel):
    """Emails-satellite fields surfaced on item detail (#200).

    Name lists are positional parallels of their addr lists ("" = mailbox had
    no display name); rows written before the #199 re-ingest may have empty
    name/bcc lists alongside populated addr lists.
    """

    message_id: str | None
    in_reply_to: str | None
    thread_key: str
    from_addr: str | None
    from_name: str | None
    to_addrs: list[str]
    to_names: list[str]
    cc_addrs: list[str]
    cc_names: list[str]
    bcc_addrs: list[str]
    labels: list[str]
    attachments: list[AttachmentDetail]


class MessageDetail(BaseModel):
    """Messages-satellite fields surfaced on item detail (#142).

    media lists metadata-only references to the chat's exported media files —
    no sizes or hashes until pixel ingestion lands (P6).
    """

    chat_key: str
    chat_name: str | None
    sender: str | None
    is_media: bool
    media: list[AttachmentDetail]


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
    # Satellite detail (#200): hydrated only for kinds with a SATELLITE_READER.
    email: EmailDetail | None = None
    message: MessageDetail | None = None


class ItemSort(StrEnum):
    """Sort orders for item listing; undated items always sort last on ts."""

    TS_DESC = "ts_desc"
    TS_ASC = "ts_asc"
    ID_DESC = "id_desc"
    ID_ASC = "id_asc"


class ListItemsRequest(BaseModel):
    """Parameters for browsing items without a search query."""

    # List caps keep the rendered SQL well under SQLite's host-parameter
    # limit (one ? per entry) — oversized lists are a validation error, not
    # an sqlite3.OperationalError. Same caps as SearchRequest.
    kinds: list[ItemKind] | None = Field(default=None, max_length=16)
    # sources.name values; unknown names match nothing.
    sources: list[str] | None = Field(default=None, max_length=64)
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
