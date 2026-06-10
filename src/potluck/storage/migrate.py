"""Schema migrations: numbered ``NNN_*.sql`` files keyed on ``PRAGMA user_version``.

Each migration executes inside one transaction together with its
``user_version`` bump (``user_version`` writes are transactional), so a
partial failure rolls the whole migration back.
"""

import re
import sqlite3
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from potluck.core.errors import MigrationError

_MIGRATION_NAME = re.compile(r"^(\d{3})_[\w-]+\.sql$")


def _builtin_migrations() -> Traversable:
    return files("potluck.storage") / "migrations"


def _load(source: Traversable | Path) -> list[tuple[int, str, str]]:
    entries: list[tuple[int, str, str]] = []
    seen: dict[int, str] = {}
    for item in source.iterdir():
        match = _MIGRATION_NAME.match(item.name)
        if not match:
            continue
        number = int(match.group(1))
        if number in seen:
            raise MigrationError(
                f"duplicate migration number {number:03d}: {seen[number]} and {item.name}"
            )
        seen[number] = item.name
        entries.append((number, item.name, item.read_text(encoding="utf-8")))
    entries.sort()
    return entries


def apply_migrations(conn: sqlite3.Connection, migrations: Traversable | Path | None = None) -> int:
    """Apply pending migrations and return the resulting schema version.

    Re-running is a no-op: only files numbered above the current
    ``user_version`` are applied.
    """
    source = migrations if migrations is not None else _builtin_migrations()
    current: int = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, name, sql in _load(source):
        if number <= current:
            continue
        script = f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;"
        try:
            conn.executescript(script)
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(f"migration {name} failed: {exc}") from exc
        current = number
    return current
