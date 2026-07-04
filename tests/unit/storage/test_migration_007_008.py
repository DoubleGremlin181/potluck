"""Migrations 007 (drop FTS prefix indexes) + 008 (email name/bcc columns), #199."""

import shutil
import sqlite3
from importlib.resources import files
from pathlib import Path

from potluck.services.context import AppContext
from potluck.storage.db import connect
from potluck.storage.migrate import apply_migrations
from tests.conftest import insert_import, insert_item, insert_source

_PRE_007 = (
    "001_meta.sql",
    "002_schema_v1.sql",
    "003_items_fts.sql",
    "004_upsert_identity.sql",
    "005_emails.sql",
    "006_archive_scans.sql",
)


def _table_sql(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    assert row is not None, f"table {name} missing"
    return str(row[0])


def test_items_fts_has_no_prefix_indexes(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        sql = _table_sql(conn, "items_fts")
    assert "prefix" not in sql
    # The rest of the FTS configuration must survive the rebuild untouched.
    assert "tokenize = 'unicode61 remove_diacritics 2'" in sql
    assert "content = 'items'" in sql


def test_emails_new_columns_with_defaults(ctx: AppContext) -> None:
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
    with ctx.db.read() as conn:
        row = conn.execute(
            "SELECT from_name, to_names_json, cc_names_json, bcc_json FROM emails"
            " WHERE item_id = ?",
            (item_id,),
        ).fetchone()
    assert row["from_name"] is None
    assert row["to_names_json"] == "[]"
    assert row["cc_names_json"] == "[]"
    assert row["bcc_json"] == "[]"


def test_007_008_apply_on_populated_v6_database(tmp_path: Path) -> None:
    """Upgrading a v6 DB rebuilds the FTS index over existing rows and adds
    the email columns without losing data."""
    old_migrations = tmp_path / "migrations"
    old_migrations.mkdir()
    source_dir = files("potluck.storage") / "migrations"
    for name in _PRE_007:
        shutil.copy(str(source_dir / name), old_migrations / name)

    db_path = tmp_path / "old.db"
    conn = connect(db_path)
    try:
        assert apply_migrations(conn, old_migrations) == 6
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        item_id = insert_item(conn, sid, iid, content_hash="h1", kind="email", title="garden notes")
        conn.execute(
            """INSERT INTO emails (item_id, thread_key, to_json, cc_json, labels_json)
               VALUES (?, 'tk', '[]', '[]', '[]')""",
            (item_id,),
        )
        old_fts = _table_sql(conn, "items_fts")
        assert "prefix" in old_fts  # sanity: v6 still carries prefix indexes

        assert apply_migrations(conn) >= 8
        assert "prefix" not in _table_sql(conn, "items_fts")

        # Rebuild repopulated the index from items: full-term and prefix
        # queries (term-range scan now) and snippet() must all still work.
        hit = conn.execute(
            "SELECT rowid, snippet(items_fts, -1, '[', ']', '…', 4) FROM items_fts"
            " WHERE items_fts MATCH '\"garden\"'"
        ).fetchone()
        assert hit is not None and hit[0] == item_id
        prefix_hit = conn.execute(
            "SELECT rowid FROM items_fts WHERE items_fts MATCH '\"gard\"*'"
        ).fetchone()
        assert prefix_hit is not None and prefix_hit[0] == item_id

        row = conn.execute("SELECT from_name, bcc_json FROM emails").fetchone()
        assert row[0] is None and row[1] == "[]"
    finally:
        conn.close()


def test_fts_triggers_still_sync_after_rebuild(ctx: AppContext) -> None:
    """The 003 triggers reference items_fts by name — they must keep the
    rebuilt (prefix-less) table in sync on insert/update/delete."""

    def _setup(conn: sqlite3.Connection) -> int:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        return insert_item(conn, sid, iid, content_hash="h1", text="cedar dahlia")

    item_id = ctx.db.write(_setup)

    def _match_count(token: str) -> int:
        with ctx.db.read() as conn:
            return int(
                conn.execute(
                    "SELECT count(*) FROM items_fts WHERE items_fts MATCH ?", (f'"{token}"',)
                ).fetchone()[0]
            )

    assert _match_count("cedar") == 1
    ctx.db.write(
        lambda conn: conn.execute("UPDATE items SET text = 'ember fern' WHERE id = ?", (item_id,))
    )
    assert _match_count("cedar") == 0
    assert _match_count("ember") == 1
    ctx.db.write(lambda conn: conn.execute("DELETE FROM items WHERE id = ?", (item_id,)))
    assert _match_count("ember") == 0
