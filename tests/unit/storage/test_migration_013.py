"""Migration 013: locations satellite table (coordinates + place identity)."""

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


def test_locations_table_exists_and_strict(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        sql = _table_sql(conn, "locations")
    assert "STRICT" in sql
    for column in ("item_id", "lat", "lon", "end_lat", "end_lon", "place_id", "semantic_type"):
        assert column in sql


def test_locations_place_index_exists(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        names = {
            str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    assert "idx_locations_place_id" in names


def test_locations_cascade_delete_with_item(ctx: AppContext) -> None:
    def _setup(conn: sqlite3.Connection) -> int:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        item_id = insert_item(conn, sid, iid, content_hash="h1", kind="location")
        conn.execute(
            "INSERT INTO locations (item_id, lat, lon) VALUES (?, 40.05, -75.15)",
            (item_id,),
        )
        return item_id

    item_id = ctx.db.write(_setup)
    ctx.db.write(lambda conn: conn.execute("DELETE FROM items WHERE id = ?", (item_id,)))
    with ctx.db.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
    assert count == 0


@pytest.mark.parametrize("column", ["lat", "lon"])
def test_coordinates_are_not_nullable(ctx: AppContext, column: str) -> None:
    """A location row without coordinates must be unrepresentable — items.lat/
    lon are nullable for every kind, so the satellite owns this invariant."""

    def _setup(conn: sqlite3.Connection) -> int:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        return insert_item(conn, sid, iid, content_hash="h1", kind="location")

    item_id = ctx.db.write(_setup)
    other = "lon" if column == "lat" else "lat"

    def _bad_insert(conn: sqlite3.Connection) -> None:
        conn.execute(
            f"INSERT INTO locations (item_id, {other}) VALUES (?, 1.0)",  # noqa: S608
            (item_id,),
        )

    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.write(_bad_insert)


def test_lat_rejects_text_affinity(ctx: AppContext) -> None:
    """STRICT REAL column: a string coordinate can never sneak into storage."""

    def _setup(conn: sqlite3.Connection) -> int:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        return insert_item(conn, sid, iid, content_hash="h1", kind="location")

    item_id = ctx.db.write(_setup)

    def _bad_insert(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO locations (item_id, lat, lon) VALUES (?, '40.05°', -75.15)",
            (item_id,),
        )

    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.write(_bad_insert)
