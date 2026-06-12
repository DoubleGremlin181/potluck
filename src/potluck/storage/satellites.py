"""Per-kind satellite writers: kind-specific tables riding the items batch.

The engine stays kind-agnostic: after writing items rows it hands each
(draft, item_id) group to its kind's writer inside the same transaction —
a new satellite kind is one entry here, zero engine changes. Kinds without
an entry (e.g. NOTE) pay nothing.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from potluck.models.drafts import EmailDraft, ItemDraft
from potluck.models.items import ItemKind
from potluck.storage.emails import draft_to_email_row, insert_emails, resolve_email_parents


@dataclass(frozen=True)
class SatelliteWriter:
    """Batch hooks for one ItemKind's satellite table.

    write_batch persists rows for (draft, item_id) pairs inside the batch
    transaction (batch-first: one executemany per statement shape).
    finalize, when present, runs once at end of import — set-based
    reconciliation that needs the whole corpus (e.g. email parent linking).
    """

    write_batch: Callable[[sqlite3.Connection, list[tuple[ItemDraft, int]]], None]
    finalize: Callable[[sqlite3.Connection, int], None] | None = None


def _write_email_batch(conn: sqlite3.Connection, pairs: list[tuple[ItemDraft, int]]) -> None:
    rows = [
        draft_to_email_row(draft, item_id)
        for draft, item_id in pairs
        if isinstance(draft, EmailDraft)
    ]
    insert_emails(conn, rows)


def _finalize_emails(conn: sqlite3.Connection, source_id: int) -> None:
    resolve_email_parents(conn, source_id)


SATELLITE_WRITERS: Final[dict[ItemKind, SatelliteWriter]] = {
    ItemKind.EMAIL: SatelliteWriter(write_batch=_write_email_batch, finalize=_finalize_emails),
}
