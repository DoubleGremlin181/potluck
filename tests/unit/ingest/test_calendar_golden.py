"""Golden test (#146): the committed Calendar fixture yields exact results.

The fixture is generator output only (see tests/fixtures/README.md); the
regeneration one-liner lives in potluck/testing/calendar.py. 20 indexed
events plus the fixed anchors (DST pair, all-day, bounded series + override,
folded/escaped, floating, attendees) in "Synthetic Life", beside a
subscription duplicated across two members ("Synthetic Rota" + its "(1)"
copy) and the meet_settings.json decoy — the real 2025-12 export's shape.

The pinned-literal assertions are the acceptance criteria made executable:
exact stored UTC instants across a DST boundary, the all-day representation,
and the recurrence policy (one item per VEVENT, no expansion).
"""

import logging
from pathlib import Path

import pytest

from potluck.services.context import AppContext
from potluck.services.imports import import_path
from potluck.testing.calendar import (
    expected_all_day_count,
    expected_cross_file_duplicate_count,
    expected_draft_count,
    expected_item_count,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "calendar" / "calendar-synth-001"

GOLDEN_COUNT = 20
GOLDEN_SEED = 11  # fixture regeneration argument, pinned for the record

# Closed-form results for count=20, pinned as literals so BOTH generator
# drift and parser drift are caught.
GOLDEN_ITEMS = 33  # 20 indexed + 8 anchors + 5 rota events
GOLDEN_DRAFTS = 37  # + the 4 rota events duplicated into the (1) copy
GOLDEN_DUPLICATES = 4
GOLDEN_ALL_DAY = 6  # 1 anchor + indexed i % 4 == 3

# DST acceptance criterion: noon America/New_York on both sides of the
# 2024-03-10 spring-forward — same wall clock, different UTC instants.
GOLDEN_DST_BEFORE_TS = "2024-03-09T17:00:00+00:00"  # noon EST (-5)
GOLDEN_DST_AFTER_TS = "2024-03-11T16:00:00+00:00"  # noon EDT (-4)

# All-day acceptance criterion: UTC midnight + the all_day flag.
GOLDEN_ALL_DAY_TS = "2024-07-15T00:00:00+00:00"
GOLDEN_ALL_DAY_END = "2024-07-16T00:00:00+00:00"

# Recurrence policy: the 8-occurrence series is ONE master item (rule in
# meta, verbatim) plus ONE override item — never 8 expanded occurrences.
GOLDEN_MASTER_EID = "ics:weekly-sync@potluck.test"
GOLDEN_MASTER_TS = "2024-03-05T08:30:00+00:00"  # 14:00 Asia/Kolkata
GOLDEN_RRULE = "FREQ=WEEKLY;COUNT=8;BYDAY=TU"
GOLDEN_EXDATES = 3
GOLDEN_OVERRIDE_EID = "ics:weekly-sync@potluck.test:20240402T083000Z"
GOLDEN_OVERRIDE_TS = "2024-04-02T09:30:00+00:00"  # moved to 15:00 Asia/Kolkata

GOLDEN_FLOATING_TS = "2024-05-01T09:00:00+00:00"  # no TZID → UTC policy

GOLDEN_FOLDED_TITLE = (
    "Quarterly planning, review; and a deliberately overlong synthetic summary that folds"
)
GOLDEN_ESCAPED_TEXT = (
    "Agenda line one\nAgenda line two, with an escaped comma\nHarbor House, Suite 7"
)


def test_fixture_exists() -> None:
    for member in (
        "Takeout/Calendar/Synthetic Life.ics",
        "Takeout/Calendar/Synthetic Rota.ics",
        "Takeout/Calendar/Synthetic Rota(1).ics",
        "Takeout/Calendar/meet_settings.json",
    ):
        assert (FIXTURE / member).is_file(), member


def test_closed_forms_match_pinned_literals() -> None:
    assert expected_item_count(GOLDEN_COUNT) == GOLDEN_ITEMS
    assert expected_draft_count(GOLDEN_COUNT) == GOLDEN_DRAFTS
    assert expected_cross_file_duplicate_count() == GOLDEN_DUPLICATES
    assert expected_all_day_count(GOLDEN_COUNT) == GOLDEN_ALL_DAY


def test_golden_import_counts(ctx: AppContext, caplog: pytest.LogCaptureFixture) -> None:
    """items_updated == 0 pins the dedup posture: the rota copies differ only
    in exporter bookkeeping (CREATED/LAST-MODIFIED/DTSTAMP), which is never
    stored, so cross-member copies dedup instead of updating."""
    with caplog.at_level(logging.WARNING):
        [run] = import_path(ctx, FIXTURE)
    assert run.source == "calendar"
    assert run.status == "completed"
    assert run.items_new == GOLDEN_ITEMS
    assert run.items_duplicate == GOLDEN_DUPLICATES
    assert run.items_updated == 0
    assert not [r for r in caplog.records if r.name.startswith("potluck")]

    with ctx.db.read() as conn:
        kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM items GROUP BY kind").fetchall())
        undated = conn.execute("SELECT COUNT(*) FROM items WHERE ts IS NULL").fetchone()[0]
    assert kinds == {"event": GOLDEN_ITEMS}
    assert undated == 0


def test_golden_dst_boundary_timestamps(ctx: AppContext) -> None:
    """THE timezone acceptance criterion: the same local wall clock on both
    sides of a DST boundary stores two different exact UTC instants."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        rows = dict(
            conn.execute(
                "SELECT external_id, ts FROM items WHERE external_id IN (?, ?)",
                ("ics:dst-before@potluck.test", "ics:dst-after@potluck.test"),
            ).fetchall()
        )
    assert rows["ics:dst-before@potluck.test"] == GOLDEN_DST_BEFORE_TS
    assert rows["ics:dst-after@potluck.test"] == GOLDEN_DST_AFTER_TS


def test_golden_all_day_representation(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        ts, all_day, end = conn.execute(
            "SELECT ts, json_extract(meta, '$.all_day'), json_extract(meta, '$.end') "
            "FROM items WHERE external_id = ?",
            ("ics:all-day-fair@potluck.test",),
        ).fetchone()
        flagged = conn.execute(
            "SELECT COUNT(*) FROM items WHERE json_extract(meta, '$.all_day')"
        ).fetchone()[0]
    assert ts == GOLDEN_ALL_DAY_TS
    assert all_day == 1
    assert end == GOLDEN_ALL_DAY_END
    assert flagged == GOLDEN_ALL_DAY


def test_golden_recurrence_policy(ctx: AppContext) -> None:
    """One item per VEVENT: the bounded 8-occurrence series imports as
    exactly TWO rows — the master (rule verbatim in meta) and the moved
    occurrence (own identity, own row) — never 8 expanded occurrences."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        series = conn.execute(
            "SELECT COUNT(*) FROM items WHERE external_id LIKE ?",
            (GOLDEN_MASTER_EID + "%",),
        ).fetchone()[0]
        master_ts, rrule, exdates = conn.execute(
            "SELECT ts, json_extract(meta, '$.rrule'), json_extract(meta, '$.exdate_count') "
            "FROM items WHERE external_id = ?",
            (GOLDEN_MASTER_EID,),
        ).fetchone()
        override_ts, recurrence_id, status = conn.execute(
            "SELECT ts, json_extract(meta, '$.recurrence_id'), "
            "json_extract(meta, '$.status') FROM items WHERE external_id = ?",
            (GOLDEN_OVERRIDE_EID,),
        ).fetchone()
    assert series == 2
    assert master_ts == GOLDEN_MASTER_TS
    assert rrule == GOLDEN_RRULE
    assert exdates == GOLDEN_EXDATES
    assert override_ts == GOLDEN_OVERRIDE_TS
    assert recurrence_id == "20240402T083000Z"
    assert status == "CANCELLED"  # the moved occurrence was then cancelled


def test_golden_floating_time_is_utc(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        [ts] = conn.execute(
            "SELECT ts FROM items WHERE external_id = ?", ("ics:floating@potluck.test",)
        ).fetchone()
    assert ts == GOLDEN_FLOATING_TS


def test_golden_folded_and_escaped_text(ctx: AppContext) -> None:
    """Folded physical lines reassemble and \\, \\; \\n unescape exactly."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        title, text = conn.execute(
            "SELECT title, text FROM items WHERE external_id = ?",
            ("ics:folded-escaped@potluck.test",),
        ).fetchone()
    assert title == GOLDEN_FOLDED_TITLE
    assert text == GOLDEN_ESCAPED_TEXT


def test_golden_attendee_privacy(ctx: AppContext) -> None:
    """Attendees survive as a count + organizer flag only — no address, no
    mailto, no alarm blurb anywhere in the stored rows."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        count, organizer, status = conn.execute(
            "SELECT json_extract(meta, '$.attendee_count'), "
            "json_extract(meta, '$.has_organizer'), json_extract(meta, '$.status') "
            "FROM items WHERE external_id = ?",
            ("ics:team-plan@potluck.test",),
        ).fetchone()
        leaks = conn.execute(
            "SELECT COUNT(*) FROM items "
            "WHERE COALESCE(title, '') || ' ' || COALESCE(text, '') || meta LIKE '%mailto%' "
            "OR meta LIKE '%attendee-%' OR meta LIKE '%organizer@%'"
        ).fetchone()[0]
        blurbs = conn.execute(
            "SELECT COUNT(*) FROM items WHERE COALESCE(text, '') LIKE '%alarm blurb%'"
        ).fetchone()[0]
    assert count == 3
    assert organizer == 1
    assert status == "TENTATIVE"
    assert leaks == 0
    assert blurbs == 0


def test_golden_cross_file_dedup(ctx: AppContext) -> None:
    """The duplicated subscription (real export: four copies of one
    calendar, 386 shared UIDs) imports each shared event ONCE."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        rota = conn.execute(
            "SELECT COUNT(*) FROM items WHERE external_id LIKE 'ics:rota-%'"
        ).fetchone()[0]
        calendars = {
            str(r[0])
            for r in conn.execute(
                "SELECT DISTINCT json_extract(meta, '$.calendar') FROM items"
            ).fetchall()
        }
    assert rota == 5  # 4 shared + the copy's one extra
    assert calendars == {"Synthetic Life", "Synthetic Rota"}


def test_golden_reimport_is_noop(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    [run2] = import_path(ctx, FIXTURE)
    assert run2.items_new == 0
    assert run2.items_duplicate == GOLDEN_DRAFTS
    assert run2.items_updated == 0
