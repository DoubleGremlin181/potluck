"""Migration 012: transactions satellite table (exact integer money)."""

import sqlite3

import pytest

from potluck.services.context import AppContext
from tests.conftest import insert_import, insert_item, insert_source


def _table_sql(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    assert row is not None, f"table {name} missing"
    return str(row[0])


def test_transactions_table_exists_and_strict(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        sql = _table_sql(conn, "transactions")
    assert "STRICT" in sql
    for column in ("item_id", "amount_milliunits", "account", "payee", "category"):
        assert column in sql


def test_transactions_indexes_exist(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        names = {
            str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    assert "idx_transactions_account" in names
    assert "idx_transactions_payee" in names
    assert "idx_transactions_category" in names


def test_transactions_cascade_delete_with_item(ctx: AppContext) -> None:
    def _setup(conn: sqlite3.Connection) -> int:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        item_id = insert_item(conn, sid, iid, content_hash="h1", kind="transaction")
        conn.execute(
            "INSERT INTO transactions (item_id, amount_milliunits) VALUES (?, -4990)",
            (item_id,),
        )
        return item_id

    item_id = ctx.db.write(_setup)
    ctx.db.write(lambda conn: conn.execute("DELETE FROM items WHERE id = ?", (item_id,)))
    with ctx.db.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert count == 0


def test_amount_is_not_nullable(ctx: AppContext) -> None:
    """Integer milliunits are the acceptance criterion — a transaction row
    without an exact amount must be unrepresentable."""

    def _setup(conn: sqlite3.Connection) -> int:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        return insert_item(conn, sid, iid, content_hash="h1", kind="transaction")

    item_id = ctx.db.write(_setup)

    def _bad_insert(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO transactions (item_id, amount_milliunits) VALUES (?, NULL)",
            (item_id,),
        )

    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.write(_bad_insert)


def test_amount_rejects_float_affinity(ctx: AppContext) -> None:
    """STRICT INTEGER column: a float amount can never sneak into storage."""

    def _setup(conn: sqlite3.Connection) -> int:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        return insert_item(conn, sid, iid, content_hash="h1", kind="transaction")

    item_id = ctx.db.write(_setup)

    def _bad_insert(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO transactions (item_id, amount_milliunits) VALUES (?, 49.9)",
            (item_id,),
        )

    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.write(_bad_insert)
