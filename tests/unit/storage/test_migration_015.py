"""Migration 015: app_settings KV table (runtime settings overrides, #151)."""

import sqlite3

import pytest

from potluck.services.context import AppContext
from potluck.storage import app_settings


def _table_sql(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    assert row is not None, f"table {name} missing"
    return str(row[0])


def test_app_settings_table_exists_and_strict(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        sql = _table_sql(conn, "app_settings")
    assert "STRICT" in sql
    assert "key" in sql
    assert "value" in sql


def test_get_setting_missing_key_is_none(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        assert app_settings.get_setting(conn, "never_set") is None


def test_set_get_round_trips_json_values(ctx: AppContext) -> None:
    """Values are JSON-encoded TEXT: booleans, numbers and strings all
    round-trip with their Python types intact."""
    values: list[tuple[str, object]] = [
        ("watch_enabled", False),
        ("watch_enabled_on", True),
        ("some_number", 42),
        ("some_string", "hello"),
    ]
    for key, value in values:
        # The closure is resolved inside this iteration (db.write blocks), so
        # late binding of the loop variables is safe.
        ctx.db.write(lambda conn: app_settings.set_setting(conn, key, value))  # noqa: B023
        with ctx.db.read() as conn:
            assert app_settings.get_setting(conn, key) == value


def test_set_setting_overwrites_in_place(ctx: AppContext) -> None:
    ctx.db.write(lambda conn: app_settings.set_setting(conn, "watch_enabled", True))
    ctx.db.write(lambda conn: app_settings.set_setting(conn, "watch_enabled", False))
    with ctx.db.read() as conn:
        assert app_settings.get_setting(conn, "watch_enabled") is False
        count = conn.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0]
    assert count == 1


def test_value_is_not_nullable(ctx: AppContext) -> None:
    """The KV never stores SQL NULL — absence is expressed by the missing row
    (a stored JSON null would be indistinguishable from a miss)."""

    def _bad_insert(conn: sqlite3.Connection) -> None:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('k', NULL)")

    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.write(_bad_insert)
