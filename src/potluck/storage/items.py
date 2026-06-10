"""Draft↔row mapping for the items table.

All items SQL is owned here; nothing outside storage/ builds items SQL.
"""

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import NamedTuple

from potluck.models.drafts import ItemDraft
from potluck.models.items import Item, ItemKind


class ItemRow(NamedTuple):
    """Column values for an items INSERT, ordered to match the canonical column list.

    Canonical INSERT order:
        source_id, import_id, kind, external_id, content_hash,
        ts, title, text, lat, lon, parent_id, meta
    """

    source_id: int
    import_id: int
    kind: str
    external_id: str | None
    content_hash: str
    ts: str | None
    title: str | None
    text: str | None
    lat: float | None
    lon: float | None
    parent_id: int | None
    meta: str


def dt_to_iso(dt: datetime) -> str:
    """Convert a datetime to an ISO-8601 UTC string."""
    return dt.astimezone(UTC).isoformat()


def iso_to_dt(value: str) -> datetime:
    """Parse an ISO-8601 string (with offset) to a tz-aware UTC datetime."""
    return datetime.fromisoformat(value).astimezone(UTC)


def draft_to_row(
    draft: ItemDraft,
    *,
    source_id: int,
    import_id: int,
    content_hash: str,
) -> ItemRow:
    """Convert a draft to an ItemRow ready for INSERT.

    parent_external_id is intentionally not resolved here — the caller
    (ledger / engine) is responsible for resolving external_id → rowid
    before calling this function.
    """
    return ItemRow(
        source_id=source_id,
        import_id=import_id,
        kind=draft.kind.value,
        external_id=draft.external_id,
        content_hash=content_hash,
        ts=dt_to_iso(draft.ts) if draft.ts is not None else None,
        title=draft.title,
        text=draft.text,
        lat=draft.lat,
        lon=draft.lon,
        parent_id=None,  # parent_external_id resolution is the engine's job
        meta=json.dumps(draft.meta, sort_keys=True),
    )


def row_to_item(row: sqlite3.Row, source_name: str) -> Item:
    """Hydrate an Item DTO from a sqlite3.Row (row_factory = sqlite3.Row)."""
    ts_raw: str | None = row["ts"]
    return Item(
        id=row["id"],
        source=source_name,
        import_id=row["import_id"],
        kind=ItemKind(row["kind"]),
        external_id=row["external_id"],
        content_hash=row["content_hash"],
        ts=iso_to_dt(ts_raw) if ts_raw is not None else None,
        title=row["title"],
        text=row["text"],
        lat=row["lat"],
        lon=row["lon"],
        parent_id=row["parent_id"],
        meta=json.loads(row["meta"]),
    )


def get_item_row(conn: sqlite3.Connection, item_id: int) -> tuple[sqlite3.Row, str] | None:
    """Fetch the items row plus its source name for the given *item_id*.

    Returns ``(row, source_name)`` if found, or ``None`` if no row matches.
    The row contains all items columns (id, source_id, import_id, kind, …).
    """
    row = conn.execute(
        """SELECT i.*, s.name AS source_name
           FROM items AS i
           JOIN sources AS s ON s.id = i.source_id
           WHERE i.id = ?""",
        (item_id,),
    ).fetchone()
    if row is None:
        return None
    return row, str(row["source_name"])


def existing_hashes(conn: sqlite3.Connection, hashes: Sequence[str]) -> set[str]:
    """Return the subset of ``hashes`` that already exist in the items table.

    Uses a single ``IN (...)`` query.  Caller guarantees len(hashes) ≤ batch
    size (2 000 max) — well under SQLite's variable limit.
    """
    if not hashes:
        return set()
    placeholders = ",".join("?" * len(hashes))
    rows = conn.execute(
        f"SELECT content_hash FROM items WHERE content_hash IN ({placeholders})",
        list(hashes),
    ).fetchall()
    return {str(row[0]) for row in rows}


def insert_items(conn: sqlite3.Connection, rows: Sequence[ItemRow]) -> None:
    """Insert a batch of ItemRows via a single ``executemany``.

    Plain ``INSERT`` (not ``OR IGNORE``) — a hash-logic bug must surface as an
    ``IntegrityError`` and roll back the batch instead of silently miscounting.
    """
    conn.executemany(
        """INSERT INTO items
               (source_id, import_id, kind, external_id, content_hash,
                ts, title, text, lat, lon, parent_id, meta)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
