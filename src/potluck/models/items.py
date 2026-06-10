"""Item DTOs."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, JsonValue


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
