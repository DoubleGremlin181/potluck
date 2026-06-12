"""Migration 005: emails satellite table + threading indexes."""

import shutil
import sqlite3
from importlib.resources import files
from pathlib import Path

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


def test_emails_table_exists_and_strict(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        sql = _table_sql(conn, "emails")
    assert "STRICT" in sql
    for column in ("item_id", "message_id", "in_reply_to", "thread_key",
                   "from_addr", "to_json", "cc_json", "labels_json"):  # fmt: skip
        assert column in sql


def test_emails_indexes_exist(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        names = {
            str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    for index in (
        "idx_emails_message_id",
        "idx_emails_in_reply_to",
        "idx_emails_thread_key",
        "idx_emails_from_addr",
        "idx_files_sha256",
    ):
        assert index in names


def test_emails_cascade_delete_with_item(ctx: AppContext) -> None:
    def _setup(conn: sqlite3.Connection) -> int:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        item_id = insert_item(conn, sid, iid, content_hash="h1", kind="email")
        conn.execute(
            """INSERT INTO emails (item_id, thread_key, to_json, cc_json, labels_json)
               VALUES (?, 'tk', '[]', '[]', '[]')""",
            (item_id,),
        )
        return item_id

    item_id = ctx.db.write(_setup)
    ctx.db.write(lambda conn: conn.execute("DELETE FROM items WHERE id = ?", (item_id,)))
    with ctx.db.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    assert count == 0


def test_005_applies_on_populated_p1_database(tmp_path: Path) -> None:
    """Upgrading a database that already holds P1 data must keep it intact."""
    old_migrations = tmp_path / "migrations"
    old_migrations.mkdir()
    source_dir = files("potluck.storage") / "migrations"
    for name in ("001_meta.sql", "002_schema_v1.sql", "003_items_fts.sql",
                 "004_upsert_identity.sql"):  # fmt: skip
        shutil.copy(str(source_dir / name), old_migrations / name)

    db_path = tmp_path / "old.db"
    conn = connect(db_path)
    try:
        assert apply_migrations(conn, old_migrations) == 4
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        insert_item(conn, sid, iid, content_hash="h1", title="kept")

        assert apply_migrations(conn) == 5
        assert _table_sql(conn, "emails")
        count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        assert count == 1
    finally:
        conn.close()
