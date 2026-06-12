"""Emails satellite: draft↔row mapping, parent reconciliation, thread queries.

All emails SQL is owned here; nothing outside storage/ builds emails SQL.
"""

import json
import sqlite3
from collections.abc import Sequence
from typing import NamedTuple

from potluck.models.drafts import EmailDraft
from potluck.storage.items import PREVIEW_CHARS


class EmailRow(NamedTuple):
    """Column values for an emails INSERT, ordered to match the column list."""

    item_id: int
    message_id: str | None
    in_reply_to: str | None
    thread_key: str
    from_addr: str | None
    to_json: str
    cc_json: str
    labels_json: str


def draft_to_email_row(draft: EmailDraft, item_id: int) -> EmailRow:
    """Convert an EmailDraft's satellite fields to an EmailRow."""
    return EmailRow(
        item_id=item_id,
        message_id=draft.message_id,
        in_reply_to=draft.in_reply_to,
        thread_key=draft.thread_key,
        from_addr=draft.from_addr,
        to_json=json.dumps(list(draft.to_addrs)),
        cc_json=json.dumps(list(draft.cc_addrs)),
        labels_json=json.dumps(list(draft.labels)),
    )


def insert_emails(conn: sqlite3.Connection, rows: Sequence[EmailRow]) -> None:
    """Insert a batch of EmailRows via a single ``executemany``.

    ``INSERT OR REPLACE`` on the item_id PK: the engine's content-update path
    reuses the existing item id, so the satellite row is rewritten in place.
    """
    conn.executemany(
        """INSERT OR REPLACE INTO emails
               (item_id, message_id, in_reply_to, thread_key, from_addr,
                to_json, cc_json, labels_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def resolve_email_parents(conn: sqlite3.Connection, source_id: int) -> int:
    """Set items.parent_id from In-Reply-To for every unlinked email of *source_id*.

    One set-based pass (no ordering assumptions): handles forward references
    within a run and children whose parent only arrived in a later import.
    Duplicate parent Message-IDs resolve deterministically to the lowest item
    id; self-replies and cross-source Message-ID collisions never link.
    Returns the number of rows linked.
    """
    cursor = conn.execute(
        """
        UPDATE items
        SET parent_id = (
            SELECT MIN(pi.id)
            FROM emails AS child
            JOIN emails AS parent_sat ON parent_sat.message_id = child.in_reply_to
            JOIN items  AS pi ON pi.id = parent_sat.item_id
            WHERE child.item_id = items.id
              AND pi.source_id = items.source_id
              AND pi.id != items.id
        )
        WHERE items.source_id = ?
          AND items.parent_id IS NULL
          AND EXISTS (
            SELECT 1
            FROM emails AS child
            JOIN emails AS parent_sat ON parent_sat.message_id = child.in_reply_to
            JOIN items  AS pi ON pi.id = parent_sat.item_id
            WHERE child.item_id = items.id
              AND pi.source_id = items.source_id
              AND pi.id != items.id
          )
        """,
        (source_id,),
    )
    return cursor.rowcount


def get_email_anchor(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    """Return (thread_key, source_id) for an email item, or None if the item
    has no emails satellite row."""
    row: sqlite3.Row | None = conn.execute(
        """SELECT e.thread_key, i.source_id
           FROM emails AS e JOIN items AS i ON i.id = e.item_id
           WHERE e.item_id = ?""",
        (item_id,),
    ).fetchone()
    return row


def list_thread_rows(
    conn: sqlite3.Connection, source_id: int, thread_key: str
) -> list[sqlite3.Row]:
    """All members of one conversation, oldest first (undated members last)."""
    return conn.execute(
        f"""SELECT i.id, i.parent_id, i.ts, i.title,
                   substr(i.text, 1, {PREVIEW_CHARS}) AS text_preview,
                   e.from_addr
            FROM emails AS e JOIN items AS i ON i.id = e.item_id
            WHERE i.source_id = ? AND e.thread_key = ?
            ORDER BY i.ts ASC NULLS LAST, i.id ASC""",
        (source_id, thread_key),
    ).fetchall()
