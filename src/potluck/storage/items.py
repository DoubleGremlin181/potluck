"""Draft↔row mapping for the items table.

All items SQL is owned here; nothing outside storage/ builds items SQL.
"""

import json
import sqlite3
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


def _dt_to_iso(dt: datetime) -> str:
    """Convert a datetime to an ISO-8601 UTC string."""
    return dt.astimezone(UTC).isoformat()


def _iso_to_dt(value: str) -> datetime:
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
        ts=_dt_to_iso(draft.ts) if draft.ts is not None else None,
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
        ts=_iso_to_dt(ts_raw) if ts_raw is not None else None,
        title=row["title"],
        text=row["text"],
        lat=row["lat"],
        lon=row["lon"],
        parent_id=row["parent_id"],
        meta=json.loads(row["meta"]),
    )
