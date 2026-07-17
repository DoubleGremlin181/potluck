"""Migration 016: suppressed_hashes registry + imports.items_suppressed (#153)."""

import sqlite3

import pytest

from potluck.services.context import AppContext
from tests.conftest import insert_import, insert_source


def _table_sql(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    assert row is not None, f"table {name} missing"
    return str(row[0])


def test_suppressed_hashes_table_shape(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        sql = _table_sql(conn, "suppressed_hashes")
    assert "STRICT" in sql
    assert "content_hash" in sql
    assert "suppressed_at" in sql


def test_suppressed_hash_is_primary_key(ctx: AppContext) -> None:
    """Re-suppressing the same content is a constraint violation, not a dup row
    — the rm/forget path relies on INSERT OR IGNORE semantics over this PK."""

    def _insert_twice(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO suppressed_hashes (content_hash, suppressed_at) VALUES ('h1', 't1')"
        )
        conn.execute(
            "INSERT INTO suppressed_hashes (content_hash, suppressed_at) VALUES ('h1', 't2')"
        )

    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.write(_insert_twice)


def test_suppressed_at_not_nullable(ctx: AppContext) -> None:
    def _bad_insert(conn: sqlite3.Connection) -> None:
        conn.execute("INSERT INTO suppressed_hashes (content_hash) VALUES ('h2')")

    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.write(_bad_insert)


def test_imports_items_suppressed_defaults_to_zero(ctx: AppContext) -> None:
    """Historical rows (and every begin_import) start the new counter at 0."""

    def _seed(conn: sqlite3.Connection) -> int:
        return insert_import(conn, insert_source(conn))

    import_id = ctx.db.write(_seed)
    with ctx.db.read() as conn:
        row = conn.execute(
            "SELECT items_suppressed FROM imports WHERE id = ?", (import_id,)
        ).fetchone()
    assert row[0] == 0
