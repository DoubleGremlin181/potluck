"""Transactions satellite: draft↔row mapping and per-item reads.

All transactions SQL is owned here; nothing outside storage/ builds
transactions SQL. Money is integer milliunits end to end (#144) — the STRICT
schema rejects anything else.
"""

import sqlite3
from collections.abc import Sequence
from typing import NamedTuple

from potluck.models.drafts import TransactionDraft


class TransactionRow(NamedTuple):
    """Column values for a transactions INSERT, ordered to match the column list."""

    item_id: int
    amount_milliunits: int
    account: str | None
    payee: str | None
    category: str | None
    category_group: str | None


def draft_to_transaction_row(draft: TransactionDraft, item_id: int) -> TransactionRow:
    """Convert a TransactionDraft's satellite fields to a TransactionRow."""
    return TransactionRow(
        item_id=item_id,
        amount_milliunits=draft.amount_milliunits,
        account=draft.account,
        payee=draft.payee,
        category=draft.category,
        category_group=draft.category_group,
    )


def insert_transactions(conn: sqlite3.Connection, rows: Sequence[TransactionRow]) -> None:
    """Insert a batch of TransactionRows via a single ``executemany``.

    ``INSERT OR REPLACE`` on the item_id PK: the engine's content-update path
    reuses the existing item id, so the satellite row is rewritten in place.
    """
    conn.executemany(
        """INSERT OR REPLACE INTO transactions
           (item_id, amount_milliunits, account, payee, category, category_group)
           VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )


def get_transaction_row(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    """One item's transactions-satellite row, or None for non-transaction items."""
    row: sqlite3.Row | None = conn.execute(
        """SELECT amount_milliunits, account, payee, category, category_group
           FROM transactions WHERE item_id = ?""",
        (item_id,),
    ).fetchone()
    return row
