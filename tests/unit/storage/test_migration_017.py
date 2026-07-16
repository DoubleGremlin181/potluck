"""Migration 017: gdrive_pulls table (Drive Takeout auto-pull tracking, #152)."""

import sqlite3
from datetime import UTC, datetime

import pytest

from potluck.models.gdrive import GDrivePullRecord
from potluck.services.context import AppContext
from potluck.storage import gdrive_pulls

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _record(
    n: int, *, set_stem: str = "takeout-20260101T000000Z", local: str | None = None
) -> GDrivePullRecord:
    return GDrivePullRecord(
        file_id=f"drive-id-{n}",
        name=f"{set_stem}-{n:03d}.zip",
        md5=f"md5-{n}",
        set_stem=set_stem,
        local_path=local or f"/downloads/{set_stem}-{n:03d}.zip",
        pulled_at=_T0,
    )


def test_user_version_is_17(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 17


def test_gdrive_pulls_table_exists_and_strict(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='gdrive_pulls'"
        ).fetchone()
    assert row is not None, "gdrive_pulls table missing"
    assert "STRICT" in str(row[0])


def test_record_and_filter_pulled_batch(ctx: AppContext) -> None:
    """filter_pulled is ONE IN(...) query over the batch (batch-first rule):
    it returns exactly the already-recorded subset."""
    ctx.db.write(lambda conn: gdrive_pulls.record_pulls(conn, [_record(1), _record(2)]))
    with ctx.db.read() as conn:
        pulled = gdrive_pulls.filter_pulled(
            conn, ["drive-id-1", "drive-id-2", "drive-id-9", "never-seen"]
        )
        count = gdrive_pulls.count_pulls(conn)
    assert pulled == {"drive-id-1", "drive-id-2"}
    assert count == 2


def test_filter_pulled_empty_input_no_query_trouble(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        assert gdrive_pulls.filter_pulled(conn, []) == set()


def test_record_pulls_is_idempotent(ctx: AppContext) -> None:
    """Re-recording the same file id replaces the row (crash-recovery re-pull
    of a set must never blow up on the already-recorded members)."""
    ctx.db.write(lambda conn: gdrive_pulls.record_pulls(conn, [_record(1)]))
    ctx.db.write(lambda conn: gdrive_pulls.record_pulls(conn, [_record(1)]))
    with ctx.db.read() as conn:
        assert gdrive_pulls.count_pulls(conn) == 1


def test_prunable_requires_completed_import_of_the_set(ctx: AppContext) -> None:
    """A pull row becomes prunable only when SOME row of its set matches a
    COMPLETED imports run by path (the watcher imports the representative
    part; one completed run covers the whole set). 'completed' is the
    schema's CHECK value — decision doc §6."""
    rep = "/downloads/takeout-20260101T000000Z-001.zip"
    ctx.db.write(lambda conn: gdrive_pulls.record_pulls(conn, [_record(1, local=rep), _record(2)]))
    with ctx.db.read() as conn:
        assert gdrive_pulls.list_prunable(conn) == []  # no import yet

    def _fake_completed_import(conn: sqlite3.Connection) -> None:
        conn.execute("INSERT OR IGNORE INTO sources (name) VALUES ('google_takeout')")
        conn.execute(
            """INSERT INTO imports (source_id, path, parser_version, started_at, status)
               VALUES ((SELECT id FROM sources WHERE name='google_takeout'), ?, 1,
                       '2026-01-01T00:00:00+00:00', 'completed')""",
            (rep,),
        )

    ctx.db.write(_fake_completed_import)
    with ctx.db.read() as conn:
        prunable = gdrive_pulls.list_prunable(conn)
    # The completed run of the representative part makes BOTH parts prunable.
    assert sorted(r.file_id for r in prunable) == ["drive-id-1", "drive-id-2"]


def test_failed_or_running_import_never_prunable(ctx: AppContext) -> None:
    rep = "/downloads/takeout-20260101T000000Z-001.zip"
    ctx.db.write(lambda conn: gdrive_pulls.record_pulls(conn, [_record(1, local=rep)]))

    def _fake_import(status: str) -> None:
        def write(conn: sqlite3.Connection) -> None:
            conn.execute("INSERT OR IGNORE INTO sources (name) VALUES ('s')")
            conn.execute(
                """INSERT INTO imports (source_id, path, parser_version, started_at,
                                        status)
                   VALUES ((SELECT id FROM sources WHERE name='s'), ?, 1,
                           '2026-01-01T00:00:00+00:00', ?)""",
                (rep, status),
            )

        ctx.db.write(write)

    _fake_import("failed")
    _fake_import("running")
    with ctx.db.read() as conn:
        assert gdrive_pulls.list_prunable(conn) == []


def test_mark_pruned_excludes_from_prunable(ctx: AppContext) -> None:
    rep = "/downloads/takeout-20260101T000000Z-001.zip"
    ctx.db.write(lambda conn: gdrive_pulls.record_pulls(conn, [_record(1, local=rep)]))

    def _completed(conn: sqlite3.Connection) -> None:
        conn.execute("INSERT OR IGNORE INTO sources (name) VALUES ('s')")
        conn.execute(
            """INSERT INTO imports (source_id, path, parser_version, started_at, status)
               VALUES ((SELECT id FROM sources WHERE name='s'), ?, 1,
                       '2026-01-01T00:00:00+00:00', 'completed')""",
            (rep,),
        )

    ctx.db.write(_completed)
    ctx.db.write(lambda conn: gdrive_pulls.mark_pruned(conn, ["drive-id-1"], _T0))
    with ctx.db.read() as conn:
        assert gdrive_pulls.list_prunable(conn) == []
        count = gdrive_pulls.count_pulls(conn)
    assert count == 1  # pruned rows stay recorded (pruned_at stamped)


def test_file_id_is_primary_key(ctx: AppContext) -> None:
    def _dup(conn: sqlite3.Connection) -> None:
        conn.execute(
            """INSERT INTO gdrive_pulls
                   (file_id, name, md5, set_stem, local_path, pulled_at)
               VALUES ('x', 'a.zip', NULL, 'a.zip', '/d/a.zip', 't'),
                      ('x', 'b.zip', NULL, 'b.zip', '/d/b.zip', 't')"""
        )

    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.write(_dup)
