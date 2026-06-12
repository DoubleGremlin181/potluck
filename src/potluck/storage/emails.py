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
    # #199 additions default for older call sites; column order matches INSERT.
    from_name: str | None = None
    to_names_json: str = "[]"
    cc_names_json: str = "[]"
    bcc_json: str = "[]"


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
        from_name=draft.from_name,
        to_names_json=json.dumps(list(draft.to_names)),
        cc_names_json=json.dumps(list(draft.cc_names)),
        bcc_json=json.dumps(list(draft.bcc_addrs)),
    )


def insert_emails(conn: sqlite3.Connection, rows: Sequence[EmailRow]) -> None:
    """Insert a batch of EmailRows via a single ``executemany``.

    ``INSERT OR REPLACE`` on the item_id PK: the engine's content-update path
    reuses the existing item id, so the satellite row is rewritten in place.
    """
    conn.executemany(
        """INSERT OR REPLACE INTO emails
               (item_id, message_id, in_reply_to, thread_key, from_addr,
                to_json, cc_json, labels_json,
                from_name, to_names_json, cc_names_json, bcc_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
    # Drive from the REPLIES, never per item. Two pathologies guarded here
    # (both hit on a real 126k-email mbox; the plan is pinned by a test):
    # 1. A correlated-subquery formulation probed every item per item — O(n²),
    #    >10 minutes.
    # 2. Even as one grouped join, without ANALYZE stats the planner drove
    #    from items via idx_items_source_hash and probed the whole source per
    #    reply (~9 minutes). CROSS JOIN is SQLite's documented join-order pin:
    #    child (partial index on in_reply_to, O(replies)) → parent_sat by
    #    message_id index → pi/ci by PK.
    cursor = conn.execute(
        """
        UPDATE items
        SET parent_id = links.parent_item_id
        FROM (
            SELECT child.item_id AS child_id, MIN(pi.id) AS parent_item_id
            FROM emails AS child
            CROSS JOIN emails AS parent_sat
            CROSS JOIN items AS pi
            CROSS JOIN items AS ci
            WHERE child.in_reply_to IS NOT NULL
              AND parent_sat.message_id = child.in_reply_to
              AND pi.id = parent_sat.item_id
              AND ci.id = child.item_id
              AND ci.source_id = ?
              AND ci.parent_id IS NULL
              AND pi.source_id = ci.source_id
              AND pi.id != child.item_id
            GROUP BY child.item_id
        ) AS links
        WHERE items.id = links.child_id
        """,
        (source_id,),
    )
    return cursor.rowcount


def get_email_row(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    """One item's full emails-satellite row, or None for non-email items (#200)."""
    row: sqlite3.Row | None = conn.execute(
        """SELECT message_id, in_reply_to, thread_key, from_addr, from_name,
                  to_json, to_names_json, cc_json, cc_names_json, bcc_json, labels_json
           FROM emails WHERE item_id = ?""",
        (item_id,),
    ).fetchone()
    return row


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
