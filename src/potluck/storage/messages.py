"""Messages satellite: draft↔row mapping and per-item reads.

All messages SQL is owned here; nothing outside storage/ builds messages SQL.
"""

import sqlite3
from collections.abc import Sequence
from typing import NamedTuple

from potluck.models.drafts import MessageDraft


class MessageRow(NamedTuple):
    """Column values for a messages INSERT, ordered to match the column list."""

    item_id: int
    chat_key: str
    chat_name: str | None
    sender: str | None
    is_media: int


def draft_to_message_row(draft: MessageDraft, item_id: int) -> MessageRow:
    """Convert a MessageDraft's satellite fields to a MessageRow."""
    return MessageRow(
        item_id=item_id,
        chat_key=draft.chat_key,
        chat_name=draft.chat_name,
        sender=draft.sender,
        is_media=int(draft.is_media),
    )


def insert_messages(conn: sqlite3.Connection, rows: Sequence[MessageRow]) -> None:
    """Insert a batch of MessageRows via a single ``executemany``.

    ``INSERT OR REPLACE`` on the item_id PK: the engine's content-update path
    reuses the existing item id, so the satellite row is rewritten in place.
    """
    conn.executemany(
        """INSERT OR REPLACE INTO messages (item_id, chat_key, chat_name, sender, is_media)
           VALUES (?, ?, ?, ?, ?)""",
        rows,
    )


def get_message_row(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    """One item's messages-satellite row, or None for non-message items."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT chat_key, chat_name, sender, is_media FROM messages WHERE item_id = ?",
        (item_id,),
    ).fetchone()
    return row
