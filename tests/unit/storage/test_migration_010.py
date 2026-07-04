"""Migration 010: items_total progress column + stale-running recovery (#132)."""

import shutil
import sqlite3
from importlib.resources import files
from pathlib import Path

import pytest

from potluck.services.context import AppContext
from potluck.storage.db import connect
from potluck.storage.imports import fail_stale_running_imports
from potluck.storage.migrate import apply_migrations
from tests.conftest import insert_import, insert_source


def test_items_total_defaults_to_null_unknown(ctx: AppContext) -> None:
    """New rows report an unknown total (NULL) unless the engine supplies one."""
    iid = ctx.db.write(lambda conn: insert_import(conn, insert_source(conn)))
    with ctx.db.read() as conn:
        value = conn.execute("SELECT items_total FROM imports WHERE id = ?", (iid,)).fetchone()[0]
    assert value is None


def test_items_total_rejects_negative(ctx: AppContext) -> None:
    iid = ctx.db.write(lambda conn: insert_import(conn, insert_source(conn)))
    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.write(
            lambda conn: conn.execute("UPDATE imports SET items_total = -1 WHERE id = ?", (iid,))
        )


def test_fail_stale_running_imports_marks_only_running_rows(ctx: AppContext) -> None:
    def _setup(conn: sqlite3.Connection) -> tuple[int, int]:
        sid = insert_source(conn)
        running = insert_import(conn, sid)  # schema default status is 'running'
        done = insert_import(conn, sid)
        conn.execute(
            "UPDATE imports SET status = 'completed',"
            " finished_at = '2024-01-01T00:00:00.000Z' WHERE id = ?",
            (done,),
        )
        return running, done

    running, done = ctx.db.write(_setup)

    assert ctx.db.write(fail_stale_running_imports) == 1

    with ctx.db.read() as conn:
        r = conn.execute(
            "SELECT status, error, finished_at FROM imports WHERE id = ?", (running,)
        ).fetchone()
        d = conn.execute("SELECT status, error FROM imports WHERE id = ?", (done,)).fetchone()
    assert (r["status"], r["error"]) == ("failed", "interrupted")
    assert r["finished_at"] is not None
    assert (d["status"], d["error"]) == ("completed", None)

    # Idempotent: nothing left to recover.
    assert ctx.db.write(fail_stale_running_imports) == 0


def test_010_applies_on_populated_database(tmp_path: Path) -> None:
    """Upgrading a v9 database keeps existing import rows; items_total is NULL."""
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
        "009_files_cascade_ledger_settings.sql",
    ):
        shutil.copy(str(source_dir / name), old_migrations / name)

    db_path = tmp_path / "old.db"
    conn = connect(db_path)
    try:
        assert apply_migrations(conn, old_migrations) == 9
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        conn.execute(
            "UPDATE imports SET status = 'completed', items_new = 7,"
            " finished_at = '2024-01-01T00:00:00.000Z' WHERE id = ?",
            (iid,),
        )
        conn.commit()

        assert apply_migrations(conn) >= 10
        row = conn.execute(
            "SELECT status, items_new, items_total FROM imports WHERE id = ?", (iid,)
        ).fetchone()
        assert tuple(row) == ("completed", 7, None)
    finally:
        conn.close()
