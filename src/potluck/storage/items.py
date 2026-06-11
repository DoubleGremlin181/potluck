"""Draft↔row mapping for the items table.

All items SQL is owned here; nothing outside storage/ builds items SQL.
"""

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Final, NamedTuple

from potluck.models.drafts import ItemDraft
from potluck.models.items import Item, ItemKind, ItemSort


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

    parent_external_id is intentionally not resolved here; engine-side
    external_id → rowid resolution arrives with the first parented source
    (P2+). Until then parent_id is always stored as NULL.
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


PREVIEW_CHARS: Final = 200

_LIST_SELECT: Final = (
    "SELECT i.id, i.kind, i.ts, i.title, "
    f"substr(i.text, 1, {PREVIEW_CHARS}) AS text_preview, "
    "s.name AS source_name "
    "FROM items AS i JOIN sources AS s ON s.id = i.source_id"
)

# The sources join only matters when filtering by source name; items.source_id
# is a NOT NULL FK, so joining never changes an unfiltered COUNT(*).
_LIST_COUNT: Final = "SELECT COUNT(*) FROM items AS i"

_LIST_COUNT_SOURCES: Final = (
    "SELECT COUNT(*) FROM items AS i JOIN sources AS s ON s.id = i.source_id"
)

# ORDER BY fragments are whitelisted per ItemSort member — user input never
# reaches the SQL string. The i.id tiebreaker keeps pagination deterministic;
# NULLS LAST puts undated items at the end in both ts directions.
_LIST_ORDER: Final[dict[ItemSort, str]] = {
    ItemSort.TS_DESC: "i.ts DESC NULLS LAST, i.id DESC",
    ItemSort.TS_ASC: "i.ts ASC NULLS LAST, i.id ASC",
    ItemSort.ID_DESC: "i.id DESC",
    ItemSort.ID_ASC: "i.id ASC",
}


def list_item_rows(
    conn: sqlite3.Connection,
    *,
    kinds: Sequence[ItemKind] | None,
    sources: Sequence[str] | None,
    since_iso: str | None,
    until_iso: str | None,
    sort: ItemSort,
    limit: int,
    offset: int,
) -> tuple[list[sqlite3.Row], int]:
    """Return one page of item summary rows plus the unpaginated total.

    Two queries under the same fully-parameterized WHERE: a COUNT(*) and the
    page SELECT, both inside one read transaction so the total and the page
    come from a single WAL snapshot (a concurrent import committed between
    them cannot make total disagree with the rows). ISO-string ts comparison
    is sound because every items.ts is written by dt_to_iso (always a +00:00
    offset); NULL ts rows never match a date filter.
    """
    where: list[str] = []
    params: list[object] = []
    if kinds:
        where.append(f"i.kind IN ({','.join('?' * len(kinds))})")
        params.extend(k.value for k in kinds)
    if sources:
        where.append(f"s.name IN ({','.join('?' * len(sources))})")
        params.extend(sources)
    if since_iso is not None:
        where.append("i.ts >= ?")
        params.append(since_iso)
    if until_iso is not None:
        where.append("i.ts < ?")
        params.append(until_iso)
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    count_sql = (_LIST_COUNT_SOURCES if sources else _LIST_COUNT) + where_sql

    own_txn = not conn.in_transaction
    if own_txn:
        conn.execute("BEGIN")
    try:
        total = int(conn.execute(count_sql, params).fetchone()[0])
        rows = conn.execute(
            f"{_LIST_SELECT}{where_sql} ORDER BY {_LIST_ORDER[sort]} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    finally:
        if own_txn:
            conn.execute("COMMIT")
    return rows, total


class ExistingItem(NamedTuple):
    """Identity-matched row state used by the engine's update/duplicate logic."""

    id: int
    content_hash: str
    meta: str  # canonical JSON text as stored (json.dumps(..., sort_keys=True))


class ContentUpdate(NamedTuple):
    """Parameters for one update_items_content row, ordered to match its SET list."""

    import_id: int
    kind: str
    ts: str | None
    title: str | None
    text: str | None
    lat: float | None
    lon: float | None
    content_hash: str
    meta: str
    id: int


class MetaUpdate(NamedTuple):
    """Parameters for one update_items_meta row, ordered to match its SET list."""

    import_id: int
    meta: str
    id: int


def existing_by_external_id(
    conn: sqlite3.Connection, source_id: int, external_ids: Sequence[str]
) -> dict[str, ExistingItem]:
    """Map each existing external_id of *source_id* to its current row state.

    Single ``IN (...)`` query per batch.  The explicit ``external_id IS NOT
    NULL`` predicate lets the planner use the partial unique index
    ``idx_items_source_external`` (migration 004).
    """
    if not external_ids:
        return {}
    placeholders = ",".join("?" * len(external_ids))
    rows = conn.execute(
        f"""SELECT external_id, id, content_hash, meta FROM items
            WHERE source_id = ? AND external_id IS NOT NULL
              AND external_id IN ({placeholders})""",
        [source_id, *external_ids],
    ).fetchall()
    return {
        str(row[0]): ExistingItem(id=int(row[1]), content_hash=str(row[2]), meta=str(row[3]))
        for row in rows
    }


def update_items_content(conn: sqlite3.Connection, rows: Sequence[ContentUpdate]) -> None:
    """Update changed-content rows via a single ``executemany``.

    title/text in the SET list make the items_fts AFTER UPDATE trigger rewrite
    the index entries — correct here, since content actually changed.
    """
    conn.executemany(
        """UPDATE items
           SET import_id = ?, kind = ?, ts = ?, title = ?, text = ?,
               lat = ?, lon = ?, content_hash = ?, meta = ?
           WHERE id = ?""",
        rows,
    )


def update_items_meta(conn: sqlite3.Connection, rows: Sequence[MetaUpdate]) -> None:
    """Refresh meta (and import provenance) via a single ``executemany``.

    Deliberately excludes title/text from the SET list: the items_fts trigger
    fires on SET-list membership even when values are unchanged, and a
    meta-only refresh of a large corpus must not rewrite the FTS index.
    """
    conn.executemany(
        "UPDATE items SET import_id = ?, meta = ? WHERE id = ?",
        rows,
    )


def existing_hashes(conn: sqlite3.Connection, source_id: int, hashes: Sequence[str]) -> set[str]:
    """Return the subset of ``hashes`` that already exist for *source_id*.

    Dedup identity is per source (UNIQUE(source_id, content_hash)): the same
    content under another source is a different logical item, never a dup.
    Uses a single ``IN (...)`` query.  Caller guarantees len(hashes) ≤ batch
    size (2 000 max) — well under SQLite's variable limit.
    """
    if not hashes:
        return set()
    placeholders = ",".join("?" * len(hashes))
    rows = conn.execute(
        f"SELECT content_hash FROM items WHERE source_id = ? AND content_hash IN ({placeholders})",
        [source_id, *hashes],
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
