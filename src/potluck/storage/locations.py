"""Locations satellite: draft↔row mapping and per-item reads.

All locations SQL is owned here; nothing outside storage/ builds locations
SQL. Coordinates are REAL end to end (#148) — the STRICT schema rejects
anything else, and lat/lon are NOT NULL (the invariant items' nullable
columns cannot express per kind).
"""

import sqlite3
from collections.abc import Sequence
from typing import NamedTuple

from potluck.models.drafts import LocationDraft


class LocationRow(NamedTuple):
    """Column values for a locations INSERT, ordered to match the column list."""

    item_id: int
    lat: float
    lon: float
    end_lat: float | None
    end_lon: float | None
    place_id: str | None
    semantic_type: str | None
    distance_m: float | None


def draft_to_location_row(draft: LocationDraft, item_id: int) -> LocationRow:
    """Convert a LocationDraft's satellite fields to a LocationRow."""
    return LocationRow(
        item_id=item_id,
        lat=draft.lat,
        lon=draft.lon,
        end_lat=draft.end_lat,
        end_lon=draft.end_lon,
        place_id=draft.place_id,
        semantic_type=draft.semantic_type,
        distance_m=draft.distance_m,
    )


def insert_locations(conn: sqlite3.Connection, rows: Sequence[LocationRow]) -> None:
    """Insert a batch of LocationRows via a single ``executemany``.

    ``INSERT OR REPLACE`` on the item_id PK: the engine's content-update path
    reuses the existing item id, so the satellite row is rewritten in place.
    """
    conn.executemany(
        """INSERT OR REPLACE INTO locations
           (item_id, lat, lon, end_lat, end_lon, place_id, semantic_type, distance_m)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def get_location_row(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    """One item's locations-satellite row, or None for non-location items."""
    row: sqlite3.Row | None = conn.execute(
        """SELECT lat, lon, end_lat, end_lon, place_id, semantic_type, distance_m
           FROM locations WHERE item_id = ?""",
        (item_id,),
    ).fetchone()
    return row
