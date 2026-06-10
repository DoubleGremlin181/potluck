"""Schema v1 migration: sources / imports / items / files tables."""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from potluck.models.items import ItemKind
from potluck.storage.db import Database, connect
from potluck.storage.migrate import apply_migrations
from tests.conftest import insert_import, insert_source

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(r[0]) for r in rows}


def _insert_item(
    conn: sqlite3.Connection,
    source_id: int,
    import_id: int,
    kind: str,
    content_hash: str,
) -> None:
    conn.execute(
        """INSERT INTO items (source_id, import_id, kind, content_hash)
           VALUES (?, ?, ?, ?)""",
        (source_id, import_id, kind, content_hash),
    )


def _make_item_inserter(
    source_id: int, import_id: int, kind: str, content_hash: str
) -> Callable[[sqlite3.Connection], None]:
    def _insert(conn: sqlite3.Connection) -> None:
        _insert_item(conn, source_id, import_id, kind, content_hash)

    return _insert


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_migration_002_applies_on_fresh_db(tmp_path: Path) -> None:
    """Database.open() applies all migrations; version == 2, all tables exist."""
    db = Database.open(tmp_path / "v1.db")
    try:
        with db.read() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        assert version == 2

        with db.read() as conn:
            tables = _table_names(conn)
        for name in ("meta", "sources", "imports", "items", "files"):
            assert name in tables, f"missing table: {name}"
    finally:
        db.close()


def test_migration_002_idempotent(tmp_path: Path) -> None:
    """Calling apply_migrations twice is a no-op; version stays 2."""
    conn = connect(tmp_path / "idem.db")
    try:
        v1 = apply_migrations(conn)
        assert v1 == 2
        v2 = apply_migrations(conn)
        assert v2 == 2
    finally:
        conn.close()


def test_kind_check_matches_enum(tmp_path: Path) -> None:
    """Every ItemKind value inserts OK; 'bogus' is rejected by the CHECK constraint."""
    db = Database.open(tmp_path / "kinds.db")
    try:
        src_id = db.write(insert_source)
        imp_id = db.write(lambda c: insert_import(c, src_id))

        for i, kind_member in enumerate(ItemKind):
            db.write(
                _make_item_inserter(
                    src_id, imp_id, kind_member.value, f"hash-{kind_member.value}-{i}"
                )
            )

        with pytest.raises(sqlite3.IntegrityError):
            db.write(_make_item_inserter(src_id, imp_id, "bogus", "hash-bogus"))
    finally:
        db.close()


def test_content_hash_unique_enforced(tmp_path: Path) -> None:
    """Duplicate content_hash raises IntegrityError."""
    db = Database.open(tmp_path / "uniq.db")
    try:
        src_id = db.write(insert_source)
        imp_id = db.write(lambda c: insert_import(c, src_id))

        db.write(_make_item_inserter(src_id, imp_id, "note", "abc123"))

        with pytest.raises(sqlite3.IntegrityError):
            db.write(_make_item_inserter(src_id, imp_id, "note", "abc123"))
    finally:
        db.close()


def test_meta_must_be_json(tmp_path: Path) -> None:
    """Non-JSON meta value is rejected by CHECK (json_valid(meta))."""
    db = Database.open(tmp_path / "meta.db")
    try:
        src_id = db.write(insert_source)
        imp_id = db.write(lambda c: insert_import(c, src_id))

        def _insert_bad_meta(conn: sqlite3.Connection) -> None:
            conn.execute(
                """INSERT INTO items (source_id, import_id, kind, content_hash, meta)
                   VALUES (?, ?, 'note', 'hash-bad-meta', 'not json')""",
                (src_id, imp_id),
            )

        with pytest.raises(sqlite3.IntegrityError):
            db.write(_insert_bad_meta)
    finally:
        db.close()
