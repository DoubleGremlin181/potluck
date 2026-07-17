"""Migration 017: gdrive_pulls table (Drive Takeout auto-pull tracking, #152)."""

import sqlite3
from datetime import UTC, datetime

import pytest

from potluck.models.gdrive import GDrivePullRecord
from potluck.services.context import AppContext
from potluck.storage import gdrive_pulls

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
# dt_to_iso(_T0) — what record_pulls persists for pulled_at below.
_T0_ISO = "2026-01-01T00:00:00+00:00"
# An import that STARTED strictly after every pulled_at — the freshness the
# prune gate demands (review I1): only such a run provably saw the whole set.
_AFTER_PULLS_ISO = "2026-01-02T00:00:00+00:00"
_BEFORE_PULLS_ISO = "2025-12-31T23:00:00+00:00"


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


def _fake_import(
    ctx: AppContext,
    *,
    path: str,
    status: str = "completed",
    started_at: str = _AFTER_PULLS_ISO,
) -> None:
    """Insert a raw imports run row (what the watcher's import leaves behind)."""

    def write(conn: sqlite3.Connection) -> None:
        conn.execute("INSERT OR IGNORE INTO sources (name) VALUES ('google_takeout')")
        conn.execute(
            """INSERT INTO imports (source_id, path, parser_version, started_at, status)
               VALUES ((SELECT id FROM sources WHERE name='google_takeout'), ?, 1, ?, ?)""",
            (path, started_at, status),
        )

    ctx.db.write(write)


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


def test_prunable_requires_completed_import_after_the_pulls(ctx: AppContext) -> None:
    """A pull row becomes prunable only when SOME row of its set matches a
    COMPLETED imports run ('completed' is the schema's CHECK value) that
    STARTED after the set's last recorded pull — only such a run provably
    opened the fully published set (opening any part loads all siblings on
    disk). Decision doc §6 + review I1."""
    rep = "/downloads/takeout-20260101T000000Z-001.zip"
    ctx.db.write(lambda conn: gdrive_pulls.record_pulls(conn, [_record(1, local=rep), _record(2)]))
    with ctx.db.read() as conn:
        assert gdrive_pulls.list_prunable(conn) == []  # no import yet

    _fake_import(ctx, path=rep, started_at=_AFTER_PULLS_ISO)
    with ctx.db.read() as conn:
        prunable = gdrive_pulls.list_prunable(conn)
    # The fresh completed run of the representative makes BOTH parts prunable.
    assert sorted(r.file_id for r in prunable) == ["drive-id-1", "drive-id-2"]


def test_stale_completed_run_never_qualifies_a_set(ctx: AppContext) -> None:
    """Review I1's exact reproduction (crash between renames): part 1 of a
    3-part set is published alone by a crashed cycle; the watcher imports it
    (a completed run at part 1's path PREDATING the recorded pulls); the next
    cycle publishes parts 2-3 and records all three pulls. That stale run
    must NOT qualify the set — parts 2-3 were never inside any completed
    import, and files.delete is destructive."""
    rep = "/downloads/takeout-20260101T000000Z-001.zip"
    # The partial-publish import completed BEFORE the pulls were recorded.
    _fake_import(ctx, path=rep, started_at=_BEFORE_PULLS_ISO)
    ctx.db.write(
        lambda conn: gdrive_pulls.record_pulls(
            conn, [_record(1, local=rep), _record(2), _record(3)]
        )
    )
    with ctx.db.read() as conn:
        assert gdrive_pulls.list_prunable(conn) == []  # stale run: no prune

    # The watcher re-imports the (now whole) set: a run postdating every
    # pulled_at qualifies all three parts.
    _fake_import(ctx, path=rep, started_at=_AFTER_PULLS_ISO)
    with ctx.db.read() as conn:
        prunable = gdrive_pulls.list_prunable(conn)
    assert sorted(r.file_id for r in prunable) == ["drive-id-1", "drive-id-2", "drive-id-3"]


def test_run_started_exactly_at_pull_time_is_not_fresh_enough(ctx: AppContext) -> None:
    """Boundary: strict '>' — a run whose started_at EQUALS the last pulled_at
    stays unqualified. When in doubt, don't prune."""
    rep = "/downloads/takeout-20260101T000000Z-001.zip"
    ctx.db.write(lambda conn: gdrive_pulls.record_pulls(conn, [_record(1, local=rep)]))
    _fake_import(ctx, path=rep, started_at=_T0_ISO)
    with ctx.db.read() as conn:
        assert gdrive_pulls.list_prunable(conn) == []


def test_repull_of_a_set_regates_older_rows(ctx: AppContext) -> None:
    """A re-record moving max(pulled_at) forward (same-name re-export pulled
    again) invalidates a previously qualifying run for the WHOLE set —
    including rows that were already prunable. Conservative by design."""
    rep = "/downloads/takeout-20260101T000000Z-001.zip"
    ctx.db.write(lambda conn: gdrive_pulls.record_pulls(conn, [_record(1, local=rep)]))
    _fake_import(ctx, path=rep, started_at=_AFTER_PULLS_ISO)
    with ctx.db.read() as conn:
        assert len(gdrive_pulls.list_prunable(conn)) == 1  # qualified …

    # … until a NEWER pull of the same set (new Drive id, same stem) lands
    # after that run started: the whole set waits for a fresh import again.
    newer = _record(9, local="/downloads/takeout-20260101T000000Z-009.zip")
    newer = newer.model_copy(update={"pulled_at": datetime(2026, 1, 3, tzinfo=UTC)})
    ctx.db.write(lambda conn: gdrive_pulls.record_pulls(conn, [newer]))
    with ctx.db.read() as conn:
        assert gdrive_pulls.list_prunable(conn) == []


def test_failed_or_running_import_never_prunable(ctx: AppContext) -> None:
    rep = "/downloads/takeout-20260101T000000Z-001.zip"
    ctx.db.write(lambda conn: gdrive_pulls.record_pulls(conn, [_record(1, local=rep)]))
    _fake_import(ctx, path=rep, status="failed", started_at=_AFTER_PULLS_ISO)
    _fake_import(ctx, path=rep, status="running", started_at=_AFTER_PULLS_ISO)
    with ctx.db.read() as conn:
        assert gdrive_pulls.list_prunable(conn) == []


def test_mark_pruned_excludes_from_prunable(ctx: AppContext) -> None:
    rep = "/downloads/takeout-20260101T000000Z-001.zip"
    ctx.db.write(lambda conn: gdrive_pulls.record_pulls(conn, [_record(1, local=rep)]))
    _fake_import(ctx, path=rep, started_at=_AFTER_PULLS_ISO)
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
