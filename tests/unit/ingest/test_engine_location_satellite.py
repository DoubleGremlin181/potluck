"""Engine satellite dispatch for locations (#148): locations rows ride the
same batch transaction, and item detail hydrates them."""

from datetime import UTC, datetime, timedelta

import pytest

from potluck.models.drafts import LocationDraft
from potluck.services.context import AppContext
from potluck.services.items import get_item
from tests.conftest import ingest_email_drafts


def _visit(n: int, *, lat: float = 40.0512345, semantic_type: str = "HOME") -> LocationDraft:
    return LocationDraft(
        external_id=f"timeline:visit:fp{n}",
        ts=datetime(2024, 3, 1, 8, tzinfo=UTC) + timedelta(hours=n),
        title="Home",
        lat=lat,
        lon=-75.1598765,
        place_id="ChIJsynthplace0001",
        semantic_type=semantic_type,
    )


def _route(n: int) -> LocationDraft:
    return LocationDraft(
        external_id=f"timeline:route:fp{n}",
        ts=datetime(2024, 3, 1, 9, tzinfo=UTC) + timedelta(hours=n),
        title="Walking — 1.2 km",
        lat=40.0512345,
        lon=-75.1598765,
        end_lat=40.0623456,
        end_lon=-75.1487654,
        semantic_type="WALKING",
        distance_m=1234.5,
    )


def _run(ctx: AppContext, *drafts: LocationDraft) -> int:
    return ingest_email_drafts(ctx, *drafts, source_name="timeline", path="/tmp/Timeline.json")


def test_import_writes_locations_satellite(ctx: AppContext) -> None:
    _run(ctx, _visit(1), _route(2))
    with ctx.db.read() as conn:
        rows = conn.execute(
            """SELECT l.lat, l.lon, l.end_lat, l.end_lon, l.place_id, l.semantic_type,
                      l.distance_m
               FROM locations l JOIN items i ON i.id = l.item_id
               ORDER BY i.ts"""
        ).fetchall()
    assert len(rows) == 2
    visit, route = rows
    assert visit["lat"] == 40.0512345
    assert visit["lon"] == -75.1598765
    assert visit["end_lat"] is None
    assert visit["place_id"] == "ChIJsynthplace0001"
    assert visit["semantic_type"] == "HOME"
    assert visit["distance_m"] is None
    assert route["end_lat"] == 40.0623456
    assert route["end_lon"] == -75.1487654
    assert route["distance_m"] == 1234.5


def test_items_row_carries_coordinates(ctx: AppContext) -> None:
    """The acceptance criterion of #148: location items carry lat/lon (the
    base items columns) for future spatial linking."""
    _run(ctx, _visit(1))
    with ctx.db.read() as conn:
        row = conn.execute("SELECT lat, lon FROM items").fetchone()
    assert row["lat"] == 40.0512345
    assert row["lon"] == -75.1598765


def test_exact_reimport_is_duplicate(ctx: AppContext) -> None:
    _run(ctx, _visit(1))
    import_id2 = _run(ctx, _visit(1))
    with ctx.db.read() as conn:
        imp = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id2,)).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
    assert int(imp["items_duplicate"]) == 1
    assert count == 1


def test_semantic_type_change_reingests_as_update(ctx: AppContext) -> None:
    """Satellite fields live inside the content hash (extra_hash_parts), so a
    re-inferred semanticType (UNKNOWN → HOME after the user confirms a place)
    must be an UPDATE that rewrites the satellite row — never a duplicate
    that silently keeps the stale type."""
    _run(ctx, _visit(1, semantic_type="UNKNOWN"))
    import_id2 = _run(ctx, _visit(1, semantic_type="HOME"))

    with ctx.db.read() as conn:
        imp = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id2,)).fetchone()
        semantic_type = conn.execute("SELECT semantic_type FROM locations").fetchone()[0]
        item_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert item_count == 1
    assert int(imp["items_updated"]) == 1
    assert semantic_type == "HOME"


def test_get_item_hydrates_location_detail(ctx: AppContext) -> None:
    _run(ctx, _route(1))
    with ctx.db.read() as conn:
        item_id = int(conn.execute("SELECT id FROM items").fetchone()[0])

    item = get_item(ctx, item_id)
    assert item.email is None
    assert item.message is None
    assert item.transaction is None
    assert item.location is not None
    assert item.location.lat == 40.0512345
    assert item.location.end_lat == 40.0623456
    assert item.location.semantic_type == "WALKING"
    assert item.location.distance_m == 1234.5
    assert item.location.place_id is None


def test_location_draft_rejects_out_of_range_coordinates() -> None:
    """lat ∈ [-90, 90], lon ∈ [-180, 180] — a parsing bug that yields an
    impossible coordinate must die at the DTO boundary, not enter storage."""
    with pytest.raises(ValueError, match="less than or equal"):
        _visit(1, lat=90.5)
    with pytest.raises(ValueError, match="lon"):
        LocationDraft(external_id="x", title="t", lat=1.0, lon=200.0)


def test_location_draft_rejects_string_coordinates() -> None:
    """Strict floats: the raw degree-sign string can never ride into lat/lon
    unparsed (garbage must be rejected, not coerced to something)."""
    with pytest.raises(ValueError, match="float"):
        LocationDraft(external_id="x", title="t", lat="40.05°", lon=-75.15)  # type: ignore[arg-type]


def test_location_draft_requires_paired_end_coordinates() -> None:
    """end_lat without end_lon (or vice versa) is always a plugin bug —
    routes carry both ends or none."""
    with pytest.raises(ValueError, match="end_lat"):
        LocationDraft(external_id="x", title="t", lat=1.0, lon=2.0, end_lat=3.0)
