"""Media satellite: draft↔row mapping and per-item reads.

All media SQL is owned here; nothing outside storage/ builds media SQL. The
byte facts (size_bytes, sha256) are NOT NULL — they come from the streamed
media bytes and can never legitimately be absent (#149).
"""

import sqlite3
from collections.abc import Sequence
from typing import NamedTuple

from potluck.models.drafts import PhotoDraft


class MediaRow(NamedTuple):
    """Column values for a media INSERT, ordered to match the column list."""

    item_id: int
    width: int | None
    height: int | None
    camera_make: str | None
    camera_model: str | None
    gps_alt: float | None
    mime: str | None
    size_bytes: int
    sha256: str


def draft_to_media_row(draft: PhotoDraft, item_id: int) -> MediaRow:
    """Convert a PhotoDraft's satellite fields to a MediaRow."""
    return MediaRow(
        item_id=item_id,
        width=draft.width,
        height=draft.height,
        camera_make=draft.camera_make,
        camera_model=draft.camera_model,
        gps_alt=draft.gps_alt,
        mime=draft.mime,
        size_bytes=draft.size_bytes,
        sha256=draft.sha256,
    )


def insert_media(conn: sqlite3.Connection, rows: Sequence[MediaRow]) -> None:
    """Insert a batch of MediaRows via a single ``executemany``.

    ``INSERT OR REPLACE`` on the item_id PK: the engine's content-update path
    reuses the existing item id, so the satellite row is rewritten in place.
    """
    conn.executemany(
        """INSERT OR REPLACE INTO media
           (item_id, width, height, camera_make, camera_model, gps_alt, mime,
            size_bytes, sha256)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def get_media_row(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    """One item's media-satellite row, or None for non-photo items."""
    row: sqlite3.Row | None = conn.execute(
        """SELECT width, height, camera_make, camera_model, gps_alt, mime,
                  size_bytes, sha256
           FROM media WHERE item_id = ?""",
        (item_id,),
    ).fetchone()
    return row
