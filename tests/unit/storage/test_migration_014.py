"""Migration 014: media satellite table (photo/video byte + EXIF facts)."""

import sqlite3

import pytest

from potluck.services.context import AppContext
from tests.conftest import insert_import, insert_item, insert_source

_SHA = "ab" * 32


def _table_sql(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    assert row is not None, f"table {name} missing"
    return str(row[0])


def test_media_table_exists_and_strict(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        sql = _table_sql(conn, "media")
    assert "STRICT" in sql
    for column in (
        "item_id",
        "width",
        "height",
        "camera_make",
        "camera_model",
        "gps_alt",
        "mime",
        "size_bytes",
        "sha256",
    ):
        assert column in sql


def test_media_sha256_index_exists(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        names = {
            str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    assert "idx_media_sha256" in names


def test_media_cascade_delete_with_item(ctx: AppContext) -> None:
    def _setup(conn: sqlite3.Connection) -> int:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        item_id = insert_item(conn, sid, iid, content_hash="h1", kind="photo")
        conn.execute(
            "INSERT INTO media (item_id, size_bytes, sha256) VALUES (?, 123, ?)",
            (item_id, _SHA),
        )
        return item_id

    item_id = ctx.db.write(_setup)
    ctx.db.write(lambda conn: conn.execute("DELETE FROM items WHERE id = ?", (item_id,)))
    with ctx.db.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    assert count == 0


@pytest.mark.parametrize("column", ["size_bytes", "sha256"])
def test_byte_facts_are_not_nullable(ctx: AppContext, column: str) -> None:
    """Every media row is derived from actual streamed bytes, so the byte
    facts (size + hash) can never legitimately be absent."""

    def _setup(conn: sqlite3.Connection) -> int:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        return insert_item(conn, sid, iid, content_hash="h1", kind="photo")

    item_id = ctx.db.write(_setup)
    other_col, other_val = ("sha256", _SHA) if column == "size_bytes" else ("size_bytes", 123)

    def _bad_insert(conn: sqlite3.Connection) -> None:
        conn.execute(
            f"INSERT INTO media (item_id, {other_col}) VALUES (?, ?)",  # noqa: S608
            (item_id, other_val),
        )

    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.write(_bad_insert)


def test_width_rejects_text_affinity(ctx: AppContext) -> None:
    """STRICT INTEGER column: a stringly width can never sneak into storage."""

    def _setup(conn: sqlite3.Connection) -> int:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        return insert_item(conn, sid, iid, content_hash="h1", kind="photo")

    item_id = ctx.db.write(_setup)

    def _bad_insert(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO media (item_id, width, size_bytes, sha256) VALUES (?, 'wide', 1, ?)",
            (item_id, _SHA),
        )

    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.write(_bad_insert)
