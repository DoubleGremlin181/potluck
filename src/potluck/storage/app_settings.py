"""app_settings table: runtime overrides for config values (#151).

All app_settings SQL is owned here; nothing outside storage/ builds it.
Values are JSON-encoded scalars; a missing row means "no override" (callers
fall back to the config value), so JSON null is never stored — it would be
indistinguishable from a miss.
"""

import json
import sqlite3


def get_setting(conn: sqlite3.Connection, key: str) -> object | None:
    """The JSON-decoded override for *key*, or None when no override exists."""
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    value: object = json.loads(str(row[0]))
    return value


def set_setting(conn: sqlite3.Connection, key: str, value: object) -> None:
    """Upsert the override for *key* (JSON-encoded)."""
    conn.execute(
        """INSERT INTO app_settings (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (key, json.dumps(value)),
    )
