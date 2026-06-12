"""archive_scans table: cached detect_sources outcomes (#196).

All archive_scans SQL is owned here; nothing outside storage/ builds it.
"""

import json
import sqlite3
from datetime import UTC, datetime

from potluck.storage.items import dt_to_iso


def get_scan(conn: sqlite3.Connection, file_hash: str, registry_fp: str) -> list[str] | None:
    """Cached matched-plugin names for this exact archive + registry, or None.

    An empty list is a valid cached outcome ("nothing matches") — distinct
    from a cache miss.
    """
    row = conn.execute(
        "SELECT matched_json FROM archive_scans WHERE file_hash = ? AND registry_fp = ?",
        (file_hash, registry_fp),
    ).fetchone()
    if row is None:
        return None
    return [str(name) for name in json.loads(str(row[0]))]


def record_scan(
    conn: sqlite3.Connection, file_hash: str, registry_fp: str, matched: list[str]
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO archive_scans
               (file_hash, registry_fp, matched_json, scanned_at)
           VALUES (?, ?, ?, ?)""",
        (file_hash, registry_fp, json.dumps(matched), dt_to_iso(datetime.now(UTC))),
    )
