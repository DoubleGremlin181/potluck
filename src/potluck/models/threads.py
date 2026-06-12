"""Thread DTOs: ordered conversations reconstructed from the emails satellite."""

from datetime import datetime

from pydantic import BaseModel


class ThreadEntry(BaseModel):
    """One message in a conversation; parent_id links the reply tree."""

    id: int
    parent_id: int | None
    ts: datetime | None
    title: str | None
    from_addr: str | None
    text_preview: str | None


class ThreadResponse(BaseModel):
    """The conversation containing the requested item, oldest message first
    (undated members last). thread_key is None when the item is not part of
    any email thread — entries then holds just the item itself."""

    item_id: int
    thread_key: str | None
    entries: list[ThreadEntry]
