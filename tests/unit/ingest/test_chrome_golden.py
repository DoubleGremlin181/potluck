"""Golden test (#145): the committed Chrome fixture yields exact results.

The fixture is generator output only (see tests/fixtures/README.md); the
regeneration one-liner lives in potluck/testing/chrome.py. 60 history records
(2 of them verbatim duplicates that import via #N suffixes) beside the
sibling-member decoys (Extensions.json, Settings.json, Dictionary.csv) the
parser must never read.

The pinned-literal assertions are the acceptance criterion made executable:
URL, title, and timestamp must come back from the DB exactly as exported —
including a microsecond-precision timestamp round-trip.
"""

import logging
from pathlib import Path

import pytest

from potluck.services.context import AppContext
from potluck.services.imports import import_path
from potluck.testing.chrome import (
    expected_duplicate_suffix_count,
    expected_empty_title_count,
    visit_time_usec,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "chrome" / "chrome-synth-001"

GOLDEN_COUNT = 60
GOLDEN_SEED = 11  # fixture regeneration argument, pinned for the record

# Closed-form results for count=60, pinned as literals so BOTH generator
# drift and parser drift are caught.
GOLDEN_DUPLICATE_SUFFIXES = 2  # i in {24, 49}
GOLDEN_EMPTY_TITLES = 6

# Identity stability anchors: chrome:<time_usec>:<sha256(url)[:16]>. These can
# only change if the identity policy itself changes — bump parser_version and
# say so in the commit if they do.
GOLDEN_FIRST_EID = "chrome:1683793800000000:a3b16c9e17842cc9"
GOLDEN_SECOND_EID = "chrome:1683793861000037:315548fd55aa23f0"

# Record 1's instant, exact to the microsecond (the µs round-trip anchor):
# _BASE_USEC + 61_000_037 µs → .000037 seconds.
GOLDEN_SECOND_TS = "2023-05-11T08:31:01.000037+00:00"
GOLDEN_SECOND_TITLE = "cliff glade meadow ridge"
GOLDEN_SECOND_TEXT = "cliff glade meadow ridge\nhttps://www.example.com/summit/quartz-1?ref=r1"

# Record 4: unicode title fidelity. Record 7: empty exported title → the
# host+path fallback, with text carrying the exact url only.
GOLDEN_UNICODE_TITLE = "hazel kelp nutmeg 🎉 Zürich"
GOLDEN_FALLBACK_TITLE = "www.example.com/valley/saffron-7"
GOLDEN_FALLBACK_TEXT = "https://www.example.com/valley/saffron-7?ref=r2"


def test_fixture_exists() -> None:
    for member in (
        "Takeout/Chrome/History.json",
        "Takeout/Chrome/Extensions.json",
        "Takeout/Chrome/Settings.json",
        "Takeout/Chrome/Dictionary.csv",
    ):
        assert (FIXTURE / member).is_file(), member


def test_closed_forms_match_pinned_literals() -> None:
    assert expected_duplicate_suffix_count(GOLDEN_COUNT) == GOLDEN_DUPLICATE_SUFFIXES
    assert expected_empty_title_count(GOLDEN_COUNT) == GOLDEN_EMPTY_TITLES
    assert visit_time_usec(1) == 1_683_793_861_000_037


def test_golden_import_counts(ctx: AppContext, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        [run] = import_path(ctx, FIXTURE)
    assert run.source == "chrome"
    assert run.status == "completed"
    assert run.items_new == GOLDEN_COUNT
    assert run.items_duplicate == 0
    assert not [r for r in caplog.records if r.name.startswith("potluck")]

    with ctx.db.read() as conn:
        kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM items GROUP BY kind").fetchall())
    assert kinds == {"activity": GOLDEN_COUNT}


def test_golden_url_title_timestamp_fidelity(ctx: AppContext) -> None:
    """THE acceptance criterion: stored values equal the exported ones
    exactly — including the µs-precision timestamp round-trip."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        ts, title, text = conn.execute(
            "SELECT ts, title, text FROM items WHERE external_id = ?", (GOLDEN_SECOND_EID,)
        ).fetchone()
    assert ts == GOLDEN_SECOND_TS  # microseconds intact
    assert title == GOLDEN_SECOND_TITLE
    assert text == GOLDEN_SECOND_TEXT


def test_golden_unicode_and_fallback_titles(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        titles = {str(r[0]) for r in conn.execute("SELECT title FROM items").fetchall()}
        [fallback_text] = [
            str(r[0])
            for r in conn.execute(
                "SELECT text FROM items WHERE title = ?", (GOLDEN_FALLBACK_TITLE,)
            ).fetchall()
        ]
        untitled = conn.execute("SELECT COUNT(*) FROM items WHERE title IS NULL").fetchone()[0]
    assert GOLDEN_UNICODE_TITLE in titles
    assert fallback_text == GOLDEN_FALLBACK_TEXT  # url only — never a derived restatement
    assert untitled == 0  # the fallback keeps every visit human-readable


def test_golden_identities_stable(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        eids = {str(r[0]) for r in conn.execute("SELECT external_id FROM items").fetchall()}
    assert GOLDEN_FIRST_EID in eids
    assert all(eid.startswith("chrome:") for eid in eids)
    suffixed = {eid for eid in eids if "#" in eid}
    assert len(suffixed) == GOLDEN_DUPLICATE_SUFFIXES
    assert all(eid.endswith("#2") for eid in suffixed)  # each dup group has 2


def test_golden_meta_shapes(ctx: AppContext) -> None:
    """Every item carries the transition + client id verbatim; the favicon
    never survives into meta; every record is dated."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        without_transition = conn.execute(
            "SELECT COUNT(*) FROM items "
            "WHERE json_extract(meta, '$.page_transition_qualifier') IS NULL"
        ).fetchone()[0]
        without_client = conn.execute(
            "SELECT COUNT(*) FROM items WHERE json_extract(meta, '$.client_id') IS NULL"
        ).fetchone()[0]
        with_favicon = conn.execute(
            "SELECT COUNT(*) FROM items WHERE json_extract(meta, '$.favicon_url') IS NOT NULL"
        ).fetchone()[0]
        undated = conn.execute("SELECT COUNT(*) FROM items WHERE ts IS NULL").fetchone()[0]
    assert without_transition == 0
    assert without_client == 0
    assert with_favicon == 0
    assert undated == 0


def test_golden_reimport_is_noop(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    [run2] = import_path(ctx, FIXTURE)
    assert run2.items_new == 0
    assert run2.items_duplicate == GOLDEN_COUNT
    assert run2.items_updated == 0
