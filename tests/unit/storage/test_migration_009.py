"""Migration 009: files ON DELETE CASCADE + settings-aware import ledger."""

import shutil
import sqlite3
from importlib.resources import files
from pathlib import Path

import pytest

from potluck.services.context import AppContext
from potluck.storage.db import connect
from potluck.storage.migrate import apply_migrations
from tests.conftest import insert_import, insert_item, insert_source


def _table_sql(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    assert row is not None, f"table {name} missing"
    return str(row[0])


def test_files_table_cascades_and_stays_strict(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        sql = _table_sql(conn, "files")
    assert "STRICT" in sql
    assert "ON DELETE CASCADE" in sql


def test_files_indexes_survive_recreation(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        names = {
            str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    assert "idx_files_item" in names
    assert "idx_files_sha256" in names


def test_deleting_item_with_files_rows_cascades(ctx: AppContext) -> None:
    """This PR is the first to populate files; deleting an email item with
    attachments must not raise IntegrityError (#198 review 21)."""

    def _setup(conn: sqlite3.Connection) -> int:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        item_id = insert_item(conn, sid, iid, content_hash="h1", kind="email")
        conn.execute(
            "INSERT INTO files (item_id, member_path, mime, size_bytes, sha256)"
            " VALUES (?, 'a.pdf', 'application/pdf', 3, ?)",
            (item_id, "aa" * 32),
        )
        return item_id

    item_id = ctx.db.write(_setup)
    ctx.db.write(lambda conn: conn.execute("DELETE FROM items WHERE id = ?", (item_id,)))
    with ctx.db.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    assert count == 0


def test_imports_extract_attachments_column(ctx: AppContext) -> None:
    def _insert(conn: sqlite3.Connection) -> int:
        sid = insert_source(conn)
        return insert_import(conn, sid)

    import_id = ctx.db.write(_insert)
    with ctx.db.read() as conn:
        value = conn.execute(
            "SELECT extract_attachments FROM imports WHERE id = ?", (import_id,)
        ).fetchone()[0]
    assert value == 0  # historical rows default to "did not extract"

    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.write(
            lambda conn: conn.execute(
                "UPDATE imports SET extract_attachments = 2 WHERE id = ?", (import_id,)
            )
        )


def test_009_applies_on_populated_database(tmp_path: Path) -> None:
    """Upgrading a v8 database with existing files rows must keep them intact."""
    old_migrations = tmp_path / "migrations"
    old_migrations.mkdir()
    source_dir = files("potluck.storage") / "migrations"
    for name in (
        "001_meta.sql",
        "002_schema_v1.sql",
        "003_items_fts.sql",
        "004_upsert_identity.sql",
        "005_emails.sql",
        "006_archive_scans.sql",
        "007_fts_drop_prefix.sql",
        "008_email_fields.sql",
    ):
        shutil.copy(str(source_dir / name), old_migrations / name)

    db_path = tmp_path / "old.db"
    conn = connect(db_path)
    try:
        assert apply_migrations(conn, old_migrations) == 8
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        item_id = insert_item(conn, sid, iid, content_hash="h1", kind="email")
        conn.execute(
            "INSERT INTO files (id, item_id, member_path, mime, size_bytes, sha256)"
            " VALUES (7, ?, 'kept.pdf', 'application/pdf', 11, ?)",
            (item_id, "cc" * 32),
        )
        conn.commit()

        assert apply_migrations(conn) >= 9
        row = conn.execute(
            "SELECT id, item_id, member_path, mime, size_bytes, sha256 FROM files"
        ).fetchone()
        assert tuple(row) == (7, item_id, "kept.pdf", "application/pdf", 11, "cc" * 32)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version >= 9
    finally:
        conn.close()
