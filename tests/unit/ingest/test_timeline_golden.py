"""Golden test (#148): the committed Timeline fixture yields exact results.

The fixture is generator output only (see tests/fixtures/README.md); the
regeneration one-liner lives in potluck/testing/timeline.py. 48 semantic
segments (visits, routes, skipped paths/memories, 2 verbatim duplicates that
import via #N suffixes) in a root-level bare Timeline.json, 48 timeline
edits (19 positions among skipped telemetry/semantic edits) in the Takeout
member, and every detection-precision decoy of the real exports.

Two import shapes are pinned: the whole directory (both formats in one run)
and the BARE Timeline.json file alone — the SingleFileArchive path that is
the primary real-world shape of the Android export.
"""

import logging
from pathlib import Path

import pytest

from potluck.models.items import ItemKind
from potluck.models.search import SearchRequest
from potluck.services.context import AppContext
from potluck.services.imports import import_path
from potluck.services.search import search
from potluck.testing.timeline import (
    expected_duplicate_suffix_count,
    expected_position_count,
    expected_route_count,
    expected_visit_count,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "timeline" / "timeline-synth-001"

GOLDEN_SEGMENTS = 48  # fixture regeneration arguments, pinned for the record
GOLDEN_SEED = 13

# Closed-form results for count=48, pinned as literals so BOTH generator
# drift and parser drift are caught.
GOLDEN_VISITS = 21
GOLDEN_ROUTES = 12
GOLDEN_POSITIONS = 19
GOLDEN_COUNT = GOLDEN_VISITS + GOLDEN_ROUTES + GOLDEN_POSITIONS
GOLDEN_DUPLICATE_SUFFIXES = 2  # segment i in {18, 37} (a route and a visit copy)
GOLDEN_LABELED_HOME_TITLES = 5  # placeId 0 visits, named by the tail profile

# Identity stability anchors: timeline:<flavor>:<sha256[:16] of the verbatim
# exported strings>. These can only change if the identity policy itself
# changes — bump parser_version and say so in the commit if they do.
GOLDEN_FIRST_VISIT_EID = "timeline:visit:7ed5b3d17870b424"
GOLDEN_FIRST_ROUTE_EID = "timeline:route:c2c6d405213f7a2e"

# Segment 1 (the first route): +05:30-offset export instant → UTC, humanized
# activity title with the km rendering, exact satellite row.
GOLDEN_FIRST_ROUTE_TS = "2024-03-01T08:45:00+00:00"
GOLDEN_FIRST_ROUTE_TITLE = "In passenger vehicle — 1.5 km"
GOLDEN_FIRST_ROUTE_ROW = (40.0154, -75.017, 40.0191, -75.0223, "IN_PASSENGER_VEHICLE", 1464.25)


def test_fixture_exists() -> None:
    for member in (
        "Timeline.json",
        "Takeout/Timeline/Timeline Edits.json",
        "Takeout/Timeline/Settings.json",
        "Takeout/Timeline/Encrypted Backups.txt",
        "Takeout/Location History/Records.json",
    ):
        assert (FIXTURE / member).is_file(), member


def test_closed_forms_match_pinned_literals() -> None:
    assert expected_visit_count(GOLDEN_SEGMENTS) == GOLDEN_VISITS
    assert expected_route_count(GOLDEN_SEGMENTS) == GOLDEN_ROUTES
    assert expected_position_count(GOLDEN_SEGMENTS) == GOLDEN_POSITIONS
    assert expected_duplicate_suffix_count(GOLDEN_SEGMENTS) == GOLDEN_DUPLICATE_SUFFIXES


def test_golden_import_counts(ctx: AppContext, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        [run] = import_path(ctx, FIXTURE)
    assert run.source == "timeline"
    assert run.status == "completed"
    assert run.items_new == GOLDEN_COUNT
    assert run.items_duplicate == 0
    assert not [r for r in caplog.records if r.name.startswith("potluck")]

    with ctx.db.read() as conn:
        kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM items GROUP BY kind").fetchall())
        types = dict(
            conn.execute(
                "SELECT json_extract(meta, '$.type'), COUNT(*) FROM items GROUP BY 1"
            ).fetchall()
        )
        satellite_rows = conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
    assert kinds == {"location": GOLDEN_COUNT}
    assert types == {
        "visit": GOLDEN_VISITS,
        "route": GOLDEN_ROUTES,
        "position": GOLDEN_POSITIONS,
    }
    assert satellite_rows == GOLDEN_COUNT


def test_golden_bare_timeline_file_imports_alone(ctx: AppContext) -> None:
    """THE acceptance shape: the bare Android Timeline.json is a plain file,
    not an archive — SingleFileArchive + the root glob alternative carry it."""
    [run] = import_path(ctx, FIXTURE / "Timeline.json")
    assert run.source == "timeline"
    assert run.items_new == GOLDEN_VISITS + GOLDEN_ROUTES
    with ctx.db.read() as conn:
        types = {
            str(r[0])
            for r in conn.execute("SELECT DISTINCT json_extract(meta, '$.type') FROM items")
        }
    assert types == {"visit", "route"}  # no Edits member in a bare-file import


def test_golden_route_fidelity(ctx: AppContext) -> None:
    """#148's acceptance criterion made executable: stored values equal the
    exported ones exactly — offset timestamp → UTC, humanized title,
    coordinates and distance in the satellite, lat/lon on the item row."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        ts, title, item_lat, item_lon = conn.execute(
            "SELECT ts, title, lat, lon FROM items WHERE external_id = ?",
            (GOLDEN_FIRST_ROUTE_EID,),
        ).fetchone()
        row = conn.execute(
            """SELECT l.lat, l.lon, l.end_lat, l.end_lon, l.semantic_type, l.distance_m
               FROM locations l JOIN items i ON i.id = l.item_id WHERE i.external_id = ?""",
            (GOLDEN_FIRST_ROUTE_EID,),
        ).fetchone()
    assert ts == GOLDEN_FIRST_ROUTE_TS
    assert title == GOLDEN_FIRST_ROUTE_TITLE
    assert tuple(row) == GOLDEN_FIRST_ROUTE_ROW
    assert (item_lat, item_lon) == GOLDEN_FIRST_ROUTE_ROW[:2]  # items row carries lat/lon


def test_golden_label_join_and_position_titles(ctx: AppContext) -> None:
    """The tail profile names placeId-0 visits "Synth Home" (the two-pass
    join); every Edits position is a "Position" with source + device meta."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        homes = conn.execute("SELECT COUNT(*) FROM items WHERE title = 'Synth Home'").fetchone()[0]
        positions = conn.execute(
            """SELECT COUNT(*) FROM items WHERE title = 'Position'
               AND json_extract(meta, '$.source') IS NOT NULL
               AND json_extract(meta, '$.device_id') IS NOT NULL"""
        ).fetchone()[0]
        undated = conn.execute("SELECT COUNT(*) FROM items WHERE ts IS NULL").fetchone()[0]
        untitled = conn.execute("SELECT COUNT(*) FROM items WHERE title IS NULL").fetchone()[0]
    assert homes == GOLDEN_LABELED_HOME_TITLES
    assert positions == GOLDEN_POSITIONS
    assert undated == 0
    assert untitled == 0  # every location item stays human-readable


def test_golden_every_location_carries_coordinates(ctx: AppContext) -> None:
    """Acceptance: visit/route items carry lat/lon for future spatial
    linking — on the items row AND the NOT NULL satellite columns."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        item_nulls = conn.execute(
            "SELECT COUNT(*) FROM items WHERE lat IS NULL OR lon IS NULL"
        ).fetchone()[0]
        route_ends = conn.execute(
            """SELECT COUNT(*) FROM locations l JOIN items i ON i.id = l.item_id
               WHERE json_extract(i.meta, '$.type') = 'route'
               AND l.end_lat IS NOT NULL AND l.end_lon IS NOT NULL"""
        ).fetchone()[0]
    assert item_nulls == 0
    assert route_ends == GOLDEN_ROUTES


def test_golden_identities_stable(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        eids = {str(r[0]) for r in conn.execute("SELECT external_id FROM items").fetchall()}
    assert GOLDEN_FIRST_VISIT_EID in eids
    assert GOLDEN_FIRST_ROUTE_EID in eids
    assert all(eid.startswith("timeline:") for eid in eids)
    suffixed = {eid for eid in eids if "#" in eid}
    assert len(suffixed) == GOLDEN_DUPLICATE_SUFFIXES
    assert all(eid.endswith("#2") for eid in suffixed)  # each dup group has 2
    assert {eid.split(":")[1] for eid in suffixed} == {"visit", "route"}


def test_golden_search_finds_locations(ctx: AppContext) -> None:
    """kind:location + a title term returns ranked location hits — the FTS
    surface the vocabulary resolution promises."""
    import_path(ctx, FIXTURE)
    response = search(ctx, SearchRequest(query='kind:location "Synth Home"'))
    assert len(response.hits) == GOLDEN_LABELED_HOME_TITLES
    assert all(hit.kind is ItemKind.LOCATION for hit in response.hits)
    assert not response.warnings


def test_golden_reimport_is_noop(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    [run2] = import_path(ctx, FIXTURE)
    assert run2.items_new == 0
    assert run2.items_duplicate == GOLDEN_COUNT
    assert run2.items_updated == 0
