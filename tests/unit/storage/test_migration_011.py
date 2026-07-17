"""Migration 011: messages satellite table (chat threading + sender)."""

import sqlite3

from potluck.services.context import AppContext
from tests.conftest import insert_import, insert_item, insert_source


def _table_sql(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    assert row is not None, f"table {name} missing"
    return str(row[0])


def test_messages_table_exists_and_strict(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        sql = _table_sql(conn, "messages")
    assert "STRICT" in sql
    for column in ("item_id", "chat_key", "chat_name", "sender", "is_media"):
        assert column in sql


def test_messages_indexes_exist(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        names = {
            str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    assert "idx_messages_chat_key" in names
    assert "idx_messages_sender" in names


def test_messages_cascade_delete_with_item(ctx: AppContext) -> None:
    def _setup(conn: sqlite3.Connection) -> int:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        item_id = insert_item(conn, sid, iid, content_hash="h1", kind="message")
        conn.execute(
            "INSERT INTO messages (item_id, chat_key) VALUES (?, 'chat')",
            (item_id,),
        )
        return item_id

    item_id = ctx.db.write(_setup)
    ctx.db.write(lambda conn: conn.execute("DELETE FROM items WHERE id = ?", (item_id,)))
    with ctx.db.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert count == 0


def test_is_media_check_constraint(ctx: AppContext) -> None:
    def _setup(conn: sqlite3.Connection) -> int:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        return insert_item(conn, sid, iid, content_hash="h1", kind="message")

    item_id = ctx.db.write(_setup)

    def _bad_insert(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO messages (item_id, chat_key, is_media) VALUES (?, 'chat', 2)",
            (item_id,),
        )

    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.write(_bad_insert)
