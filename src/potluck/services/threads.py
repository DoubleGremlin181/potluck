"""Thread retrieval service: ordered conversations around one item."""

from potluck.core.errors import ItemNotFoundError
from potluck.models.threads import ThreadEntry, ThreadResponse
from potluck.services.context import AppContext
from potluck.storage import emails as emails_storage
from potluck.storage.items import PREVIEW_CHARS, get_item_row, iso_to_dt


def get_thread(ctx: AppContext, item_id: int) -> ThreadResponse:
    """Return the conversation containing *item_id*, oldest first.

    Conversations are scoped per source: identical thread keys under two
    sources are two threads. A non-email item comes back as a single-entry
    thread with thread_key None.

    Raises ItemNotFoundError if no item has this id.
    """
    with ctx.db.read() as conn:
        anchor = emails_storage.get_email_anchor(conn, item_id)
        if anchor is not None:
            thread_key = str(anchor["thread_key"])
            rows = emails_storage.list_thread_rows(conn, int(anchor["source_id"]), thread_key)
            entries = [
                ThreadEntry(
                    id=int(row["id"]),
                    parent_id=row["parent_id"],
                    ts=iso_to_dt(row["ts"]) if row["ts"] is not None else None,
                    title=row["title"],
                    from_addr=row["from_addr"],
                    text_preview=row["text_preview"],
                )
                for row in rows
            ]
            return ThreadResponse(item_id=item_id, thread_key=thread_key, entries=entries)

        found = get_item_row(conn, item_id)
    if found is None:
        raise ItemNotFoundError(f"item {item_id} not found")
    row, _source = found
    text: str | None = row["text"]
    entry = ThreadEntry(
        id=int(row["id"]),
        parent_id=row["parent_id"],
        ts=iso_to_dt(row["ts"]) if row["ts"] is not None else None,
        title=row["title"],
        from_addr=None,
        text_preview=text[:PREVIEW_CHARS] if text is not None else None,
    )
    return ThreadResponse(item_id=item_id, thread_key=None, entries=[entry])
