"""Tests for the Google Timeline source plugin (#148).

Testing private helpers (_parse_segments, _parse_edits, _profile_labels) is
intentional: the identity policy, the degree-string coordinate discipline,
and the incremental-JSON containment are the public contract of this module
and must be covered at the unit level, from synthetic bytes.

Field names here mirror the real 2025-12 exports (shape only — all content
is synthetic, coordinates live on a fictional (40.0x, -75.0x) grid).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from potluck.ingest.plugins import ParseContext, detect_sources, discover
from potluck.ingest.readers import open_archive
from potluck.ingest.sources.timeline import (
    _parse_edits,
    _parse_segments,
    _profile_labels,
    parse,
)
from potluck.models.drafts import LocationDraft
from potluck.models.items import ItemKind
from potluck.testing.archives import write_archive

_MEMBER = "Timeline.json"
_EDITS_MEMBER = "Takeout/Timeline/Timeline Edits.json"

_START = "2024-03-01T08:00:00.000+05:30"
_END = "2024-03-01T08:30:00.000+05:30"
_LATLNG = "40.0512345°, -75.1598765°"
_END_LATLNG = "40.0623456°, -75.1487654°"
_PLACE = "ChIJsynthetic0001"


def _visit(**overrides: object) -> dict[str, Any]:
    """One visit segment in the real export's field order; None removes."""
    base: dict[str, Any] = {
        "startTime": _START,
        "endTime": _END,
        "startTimeTimezoneUtcOffsetMinutes": 330,
        "endTimeTimezoneUtcOffsetMinutes": 330,
        "visit": {
            "hierarchyLevel": 0,
            "probability": 0.82,
            "topCandidate": {
                "placeId": _PLACE,
                "semanticType": "HOME",
                "probability": 0.91,
                "placeLocation": {"latLng": _LATLNG},
            },
        },
    }
    for key, value in overrides.items():
        if value is None:
            base.pop(key, None)
        else:
            base[key] = value
    return base


def _activity(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "startTime": _START,
        "endTime": _END,
        "startTimeTimezoneUtcOffsetMinutes": 330,
        "endTimeTimezoneUtcOffsetMinutes": 330,
        "activity": {
            "start": {"latLng": _LATLNG},
            "end": {"latLng": _END_LATLNG},
            "distanceMeters": 1234.5,
            "probability": 0.77,
            "topCandidate": {"type": "WALKING", "probability": 0.88},
        },
    }
    for key, value in overrides.items():
        if value is None:
            base.pop(key, None)
        else:
            base[key] = value
    return base


def _position_edit(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "deviceId": "-4242424242424242424",
        "rawSignal": {
            "signal": {
                "position": {
                    "point": {"latE7": 400512345, "lngE7": -751598765},
                    "accuracyMm": 12000,
                    "altitudeMeters": 96.5,
                    "source": "WIFI",
                    "timestamp": "2024-03-05T10:00:00.000Z",
                    "speedMetersPerSecond": 0.25,
                }
            },
            "additionalTimestamp": "2024-03-05T10:00:01.000Z",
        },
    }
    for key, value in overrides.items():
        if value is None:
            base.pop(key, None)
        else:
            base[key] = value
    return base


def _timeline_bytes(*segments: object, profile: object = None) -> bytes:
    doc: dict[str, Any] = {"semanticSegments": list(segments), "rawSignals": []}
    if profile is not None:
        doc["userLocationProfile"] = profile
    return json.dumps(doc, ensure_ascii=False).encode()


def _drafts(
    *segments: object, labels: dict[str, str] | None = None, member: str = _MEMBER
) -> list[LocationDraft]:
    text = _timeline_bytes(*segments).decode()
    return list(_parse_segments(text, member, labels or {}))


def _edit_drafts(*edits: object, member: str = _EDITS_MEMBER) -> list[LocationDraft]:
    text = json.dumps({"timelineEdits": list(edits)}, ensure_ascii=False)
    return list(_parse_edits(text, member))


def _eid(flavor: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]
    return f"timeline:{flavor}:{digest}"


# ---------------------------------------------------------------------------
# Visit mapping
# ---------------------------------------------------------------------------


def test_visit_basic_mapping() -> None:
    [d] = _drafts(_visit())
    assert d.kind is ItemKind.LOCATION
    assert d.external_id == _eid("visit", _START, _END, _LATLNG, _PLACE)
    assert d.ts == datetime(2024, 3, 1, 2, 30, tzinfo=UTC)  # +05:30 → UTC
    assert d.title == "Home"
    assert d.text is None  # coordinates are satellite data, not prose
    assert d.lat == 40.0512345
    assert d.lon == -75.1598765
    assert d.end_lat is None
    assert d.end_lon is None
    assert d.place_id == _PLACE
    assert d.semantic_type == "HOME"
    assert d.distance_m is None
    assert d.meta == {
        "type": "visit",
        "end": "2024-03-01T03:00:00+00:00",
        "hierarchy_level": 0,
    }


def test_visit_title_prefers_profile_label() -> None:
    """The tail profile's placeId→label join names the place ("Home"-style
    user labels beat the semantic-type fallback)."""
    [d] = _drafts(_visit(), labels={_PLACE: "Synth Home"})
    assert d.title == "Synth Home"


def test_visit_label_join_misses_fall_back_to_semantic_type() -> None:
    [d] = _drafts(_visit(), labels={"ChIJsomeotherplace": "Synth Work"})
    assert d.title == "Home"


@pytest.mark.parametrize(
    ("semantic_type", "title"),
    [
        ("HOME", "Home"),
        ("WORK", "Work"),
        ("INFERRED_HOME", "Home (inferred)"),
        ("INFERRED_WORK", "Work (inferred)"),
        ("SCHOOL", "School"),  # v0's map survives
        ("GYM", "Gym"),
        ("ALIASED_LOCATION", "Saved place"),
        ("SEARCHED_ADDRESS", "Searched address"),
        ("UNKNOWN", "Unknown place"),
        ("SOME_FUTURE_TYPE", "Some future type"),  # mechanical fallback
    ],
)
def test_visit_semantic_type_titles(semantic_type: str, title: str) -> None:
    visit = _visit()
    visit["visit"]["topCandidate"]["semanticType"] = semantic_type
    [d] = _drafts(visit)
    assert d.title == title
    assert d.semantic_type == semantic_type  # satellite keeps the verbatim enum


def test_visit_without_semantic_type_titles_unknown_place() -> None:
    visit = _visit()
    del visit["visit"]["topCandidate"]["semanticType"]
    [d] = _drafts(visit)
    assert d.title == "Unknown place"
    assert d.semantic_type is None


def test_visit_hierarchy_level_rides_meta() -> None:
    """Nested child visits are kept (they overwhelmingly name places their
    level-0 parents do not — real-data evidence in the module docstring);
    the level rides meta so they stay filterable."""
    visit = _visit()
    visit["visit"]["hierarchyLevel"] = 1
    [d] = _drafts(visit)
    assert d.meta["hierarchy_level"] == 1


def test_visit_without_place_id_still_imports() -> None:
    visit = _visit()
    del visit["visit"]["topCandidate"]["placeId"]
    [d] = _drafts(visit)
    assert d.place_id is None
    assert d.external_id == _eid("visit", _START, _END, _LATLNG, "")


def test_visit_missing_end_time_keeps_item_without_meta_end() -> None:
    [d] = _drafts(_visit(endTime=None))
    assert d.ts is not None
    assert "end" not in d.meta
    assert d.external_id == _eid("visit", _START, "", _LATLNG, _PLACE)


def test_visit_unparseable_start_time_keeps_item_undated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Identity hashes the raw strings, so the item survives a foreign
    timestamp shape — undated, with one latched WARNING per member."""
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(_visit(startTime="last Tuesday"), _visit(startTime="a while ago"))
    assert len(drafts) == 2
    assert all(d.ts is None for d in drafts)
    assert len([r for r in caplog.records if "timestamp" in r.message]) == 1  # latched


# ---------------------------------------------------------------------------
# Coordinate parsing: strict degree strings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "garbage",
        "40.05, -75.15",  # no degree signs
        "40.0512345°",  # one coordinate
        "40.05° -75.15°",  # no comma
        "abc°, def°",
        "95.0000000°, -75.1598765°",  # latitude out of range
        "40.0512345°, -190.0000000°",  # longitude out of range
        1234,
        {"lat": 40.05},
    ],
)
def test_visit_with_unusable_lat_lng_is_skipped_with_warning(
    bad: object, caplog: pytest.LogCaptureFixture
) -> None:
    """A location item without coordinates is meaningless; garbage must be
    rejected — never parsed into 0.0 (the v0 regex accepted junk prefixes)."""
    visit = _visit()
    if bad is None:
        del visit["visit"]["topCandidate"]["placeLocation"]
    else:
        visit["visit"]["topCandidate"]["placeLocation"]["latLng"] = bad
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(visit, _visit())
    assert len(drafts) == 1  # the good sibling still imports
    assert any("coordinates" in r.message for r in caplog.records)


def test_negative_coordinates_parse_exactly() -> None:
    visit = _visit()
    visit["visit"]["topCandidate"]["placeLocation"]["latLng"] = "-33.8688197°, 151.2092955°"
    [d] = _drafts(visit)
    assert d.lat == -33.8688197
    assert d.lon == 151.2092955


# ---------------------------------------------------------------------------
# Route (activity) mapping
# ---------------------------------------------------------------------------


def test_route_basic_mapping() -> None:
    [d] = _drafts(_activity())
    assert d.kind is ItemKind.LOCATION
    assert d.external_id == _eid("route", _START, _END, _LATLNG, _END_LATLNG)
    assert d.ts == datetime(2024, 3, 1, 2, 30, tzinfo=UTC)
    assert d.title == "Walking — 1.2 km"
    assert d.text is None
    assert d.lat == 40.0512345
    assert d.lon == -75.1598765
    assert d.end_lat == 40.0623456
    assert d.end_lon == -75.1487654
    assert d.place_id is None
    assert d.semantic_type == "WALKING"
    assert d.distance_m == 1234.5
    assert d.meta == {"type": "route", "end": "2024-03-01T03:00:00+00:00"}


@pytest.mark.parametrize(
    ("activity_type", "meters", "title"),
    [
        ("WALKING", 850, "Walking — 850 m"),
        ("IN_PASSENGER_VEHICLE", 12345.6, "In passenger vehicle — 12.3 km"),
        ("UNKNOWN_ACTIVITY_TYPE", 1000, "Unknown activity type — 1.0 km"),
        ("CYCLING", None, "Cycling"),
        (None, 500, "Route — 500 m"),
        (None, None, "Route"),
    ],
)
def test_route_titles(activity_type: str | None, meters: float | None, title: str) -> None:
    activity = _activity()
    if activity_type is None:
        del activity["activity"]["topCandidate"]["type"]
    else:
        activity["activity"]["topCandidate"]["type"] = activity_type
    if meters is None:
        del activity["activity"]["distanceMeters"]
    else:
        activity["activity"]["distanceMeters"] = meters
    [d] = _drafts(activity)
    assert d.title == title


def test_route_missing_end_lat_lng_keeps_start_only(caplog: pytest.LogCaptureFixture) -> None:
    """Routes carry both ends or none — a bad end degrades to a start-only
    route (the movement still happened) with one WARNING."""
    activity = _activity()
    activity["activity"]["end"] = {"latLng": "junk"}
    with caplog.at_level(logging.WARNING):
        [d] = _drafts(activity)
    assert d.end_lat is None
    assert d.end_lon is None
    assert d.lat == 40.0512345
    assert d.external_id == _eid("route", _START, _END, _LATLNG, "junk")
    assert any("end coordinates" in r.message for r in caplog.records)


def test_route_missing_start_lat_lng_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    activity = _activity()
    del activity["activity"]["start"]
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(activity)
    assert drafts == []
    assert any("coordinates" in r.message for r in caplog.records)


def test_route_non_numeric_distance_is_dropped(caplog: pytest.LogCaptureFixture) -> None:
    activity = _activity()
    activity["activity"]["distanceMeters"] = "3.2km"
    with caplog.at_level(logging.WARNING):
        [d] = _drafts(activity)
    assert d.distance_m is None
    assert d.title == "Walking"


def test_route_integer_distance_survives() -> None:
    """distanceMeters is int-valued on 84% of real routes — both JSON number
    shapes must land."""
    activity = _activity()
    activity["activity"]["distanceMeters"] = 3196
    [d] = _drafts(activity)
    assert d.distance_m == 3196.0


# ---------------------------------------------------------------------------
# Skipped segment flavors + containment
# ---------------------------------------------------------------------------


def test_timeline_path_and_memory_segments_are_silent_non_items(
    caplog: pytest.LogCaptureFixture,
) -> None:
    path_segment = {
        "startTime": _START,
        "endTime": _END,
        "timelinePath": [{"point": _LATLNG, "time": _START}],
    }
    memory_segment = {
        "startTime": _START,
        "endTime": _END,
        "startTimeTimezoneUtcOffsetMinutes": 330,
        "endTimeTimezoneUtcOffsetMinutes": 330,
        "timelineMemory": {"trip": {"distanceFromOriginKms": 42}},
    }
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(path_segment, memory_segment, _visit())
    assert len(drafts) == 1
    assert not caplog.records  # documented skips, not anomalies


def test_unknown_segment_flavor_warns_once_per_member(
    caplog: pytest.LogCaptureFixture,
) -> None:
    foreign = {"startTime": _START, "endTime": _END, "futureFlavor": {}}
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(foreign, dict(foreign), _visit())
    assert len(drafts) == 1
    assert len([r for r in caplog.records if "flavor" in r.message]) == 1  # latched


def test_non_object_segment_is_skipped_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        drafts = _drafts("stray string", _visit())
    assert len(drafts) == 1
    assert any("not an object" in r.message for r in caplog.records)


def test_malformed_json_warns_and_keeps_yielded_segments(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Truncated export mid-array: segments already yielded stand, one
    WARNING names the member, no exception escapes."""
    good = json.dumps(_visit())
    text = '{"semanticSegments": [' + good + ", {broken"
    with caplog.at_level(logging.WARNING):
        drafts = list(_parse_segments(text, _MEMBER, {}))
    assert len(drafts) == 1
    assert any("JSON error" in r.message for r in caplog.records)


def test_member_without_segments_array_warns(caplog: pytest.LogCaptureFixture) -> None:
    """A renamed/foreign shape must never import as zero items silently."""
    with caplog.at_level(logging.WARNING):
        drafts = list(_parse_segments(json.dumps({"rawSignals": []}), _MEMBER, {}))
    assert drafts == []
    assert any("semanticSegments" in r.message for r in caplog.records)


def test_empty_segments_array_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert _drafts() == []
    assert not caplog.records


# ---------------------------------------------------------------------------
# Identity: first-seen #N + per-member counters
# ---------------------------------------------------------------------------


def test_identical_segments_get_first_seen_suffixes() -> None:
    drafts = _drafts(_visit(), _visit(), _visit())
    eids = [d.external_id or "" for d in drafts]
    assert "#" not in eids[0]
    assert eids[1] == eids[0] + "#2"
    assert eids[2] == eids[0] + "#3"


def test_same_span_different_place_never_collides() -> None:
    other = _visit()
    other["visit"]["topCandidate"]["placeId"] = "ChIJsynthetic0002"
    drafts = _drafts(_visit(), other)
    assert len({d.external_id for d in drafts}) == 2


def test_semantic_type_is_not_identity() -> None:
    """Re-inference (UNKNOWN → HOME once the user confirms) must UPDATE the
    item in place, not mint a second one — the type stays out of the hash."""
    confirmed = _visit()
    confirmed["visit"]["topCandidate"]["semanticType"] = "UNKNOWN"
    [a] = _drafts(_visit())
    [b] = _drafts(confirmed)
    assert a.external_id == b.external_id


# ---------------------------------------------------------------------------
# Profile (pass 1) parsing
# ---------------------------------------------------------------------------


def test_profile_labels_extracts_labeled_places_only() -> None:
    profile = {
        "frequentPlaces": [
            {"placeId": "ChIJsynthetic0001", "placeLocation": _LATLNG, "label": "Synth Home"},
            {"placeId": "ChIJsynthetic0002", "placeLocation": _LATLNG},
            {"placeLocation": _LATLNG, "label": "orphan label"},
        ]
    }
    text = _timeline_bytes(_visit(), profile=profile).decode()
    assert _profile_labels(text, _MEMBER) == {"ChIJsynthetic0001": "Synth Home"}


def test_profile_missing_degrades_silently(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        labels = _profile_labels(_timeline_bytes(_visit()).decode(), _MEMBER)
    assert labels == {}
    assert not caplog.records


def test_profile_malformed_json_warns_and_degrades(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        labels = _profile_labels('{"semanticSegments": [], "userLocationProfile": {bro', _MEMBER)
    assert labels == {}
    assert any("profile" in r.message for r in caplog.records)


def test_profile_skim_never_builds_the_segment_trees() -> None:
    """The skim walks the arrays element-wise — a foreign scalar under the
    profile key degrades to no labels rather than failing the member."""
    text = '{"semanticSegments": [1, 2, 3], "userLocationProfile": 42}'
    assert _profile_labels(text, _MEMBER) == {}


# ---------------------------------------------------------------------------
# Timeline Edits (position records)
# ---------------------------------------------------------------------------


def test_position_basic_mapping() -> None:
    [d] = _edit_drafts(_position_edit())
    assert d.kind is ItemKind.LOCATION
    assert d.external_id == _eid(
        "position",
        "-4242424242424242424",
        "2024-03-05T10:00:00.000Z",
        "2024-03-05T10:00:01.000Z",
        "400512345",
        "-751598765",
    )
    assert d.ts == datetime(2024, 3, 5, 10, 0, tzinfo=UTC)
    assert d.title == "Position"
    assert d.lat == 40.0512345
    assert d.lon == -75.1598765
    assert d.end_lat is None
    assert d.place_id is None
    assert d.semantic_type is None
    assert d.meta == {
        "type": "position",
        "source": "WIFI",
        "device_id": "-4242424242424242424",
    }


def test_position_e7_directly_on_position_still_parses() -> None:
    """Older Edits generations put latE7/lngE7 on position itself, not under
    point — v0 handled both, so does v1."""
    edit = _position_edit()
    position = edit["rawSignal"]["signal"]["position"]
    del position["point"]
    position["latE7"] = 400512345
    position["lngE7"] = -751598765
    [d] = _edit_drafts(edit)
    assert d.lat == 40.0512345
    assert d.lon == -75.1598765


@pytest.mark.parametrize(
    "point",
    [
        {"latE7": "400512345", "lngE7": -751598765},  # string E7
        {"latE7": True, "lngE7": -751598765},  # bool is not an int here
        {"latE7": 999000000, "lngE7": -751598765},  # latitude out of range
        {"lngE7": -751598765},  # missing latE7
        "garbage",
    ],
)
def test_position_with_unusable_point_is_skipped(
    point: object, caplog: pytest.LogCaptureFixture
) -> None:
    edit = _position_edit()
    edit["rawSignal"]["signal"]["position"]["point"] = point
    with caplog.at_level(logging.WARNING):
        drafts = _edit_drafts(edit, _position_edit())
    assert len(drafts) == 1
    assert any("coordinates" in r.message for r in caplog.records)


def test_telemetry_and_semantic_edit_kinds_are_silent_non_items(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """activityRecord/wifiScan signals and inferred/userEdited semantic
    segments are documented skips (48% / 27% / 0.6% of the real file) —
    positions are the only Takeout-side location signal that imports."""
    activity_record = {
        "deviceId": "-4242424242424242424",
        "rawSignal": {"signal": {"activityRecord": {"probableActivities": []}}},
    }
    wifi = {"deviceId": "-4242424242424242424", "rawSignal": {"signal": {"wifiScan": {}}}}
    inferred = {"deviceId": "-4242424242424242424", "inferredSemanticSegment": {"segment": {}}}
    user_edited = {"deviceId": "-4242424242424242424", "userEditedSemanticSegment": {}}
    with caplog.at_level(logging.WARNING):
        drafts = _edit_drafts(activity_record, wifi, inferred, user_edited, _position_edit())
    assert len(drafts) == 1
    assert not caplog.records


def test_unknown_edit_kind_warns_once_per_member(caplog: pytest.LogCaptureFixture) -> None:
    """A FUTURE edit kind (neither rawSignal nor a documented semantic edit)
    and an unknown rawSignal type must not vanish silently — one latched
    WARNING per member, mirroring the segments pass's unknown-flavor posture
    (task-7 review Minor c)."""
    unknown_kind = {"deviceId": "-1", "placeVisitEdit": {"placeId": "p1"}}
    unknown_signal = {"deviceId": "-1", "rawSignal": {"signal": {"odometerRecord": {}}}}
    with caplog.at_level(logging.WARNING):
        drafts = _edit_drafts(unknown_kind, unknown_signal, _position_edit())
    assert len(drafts) == 1
    assert len([r for r in caplog.records if "unknown" in r.message]) == 1  # latched


def test_position_missing_timestamp_keeps_item_undated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    edit = _position_edit()
    del edit["rawSignal"]["signal"]["position"]["timestamp"]
    with caplog.at_level(logging.WARNING):
        [d] = _edit_drafts(edit)
    assert d.ts is None
    assert any("timestamp" in r.message for r in caplog.records)


def test_edits_member_without_array_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        drafts = list(_parse_edits(json.dumps({"otherKey": []}), _EDITS_MEMBER))
    assert drafts == []
    assert any("timelineEdits" in r.message for r in caplog.records)


def test_identical_position_edits_get_first_seen_suffixes() -> None:
    drafts = _edit_drafts(_position_edit(), _position_edit())
    eids = [d.external_id or "" for d in drafts]
    assert eids[1] == eids[0] + "#2"


# ---------------------------------------------------------------------------
# Detection + parse() over archives
# ---------------------------------------------------------------------------


def test_detection_matches_export_layout_precisely() -> None:
    matches = {
        # The bare Android export: at the root (SingleFileArchive exposes the
        # basename), extracted into a folder, or re-zipped under Takeout/.
        "Timeline.json": True,
        "phone-backup/Timeline.json": True,
        "wrapper/Takeout/Timeline.json": True,
        # The modern Takeout Edits file, anchored on its Timeline/ parent.
        "Takeout/Timeline/Timeline Edits.json": True,
        "Timeline/Timeline Edits.json": True,
        "wrapper/Takeout/Timeline/Timeline Edits.json": True,
        # Siblings of the real Timeline folder must never match.
        "Takeout/Timeline/Settings.json": False,
        "Takeout/Timeline/Encrypted Backups.txt": False,
        # A lone hand-extracted Edits file has no Timeline/ anchor.
        "Timeline Edits.json": False,
        # Other products' location-flavored files.
        "Takeout/Fit/All Data/derived_com.google.location.sample_com.google..json": False,
        "Takeout/News/followed_locations.txt": False,
        # The discontinued pre-2025 formats are a non-goal.
        "Takeout/Location History/Records.json": False,
        "Takeout/Location History (Timeline)/Semantic Location History/2019_JANUARY.json": False,
        # Near-misses.
        "MyTimeline.json": False,
        "Timeline.json.bak": False,
        "timeline.json": False,  # matching is case-sensitive
    }
    plugin = discover()["timeline"]
    for name, expected in matches.items():
        assert plugin.detect.matches(name) is expected, name


def test_parse_joins_labels_across_two_passes(tmp_path: Path) -> None:
    """The tail profile (AFTER the big arrays in the real file) names the
    visits — the two-pass structure is what makes the join possible."""
    profile = {
        "frequentPlaces": [{"placeId": _PLACE, "placeLocation": _LATLNG, "label": "Synth Home"}]
    }
    members = {"Timeline.json": _timeline_bytes(_visit(), profile=profile)}
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    [d] = list(parse(open_archive(archive), ParseContext()))
    assert d.title == "Synth Home"


def test_parse_bare_timeline_file_standalone(tmp_path: Path) -> None:
    """THE primary real shape: a bare Timeline.json is not an archive at all;
    SingleFileArchive + the root glob alternative make it import."""
    bare = tmp_path / "Timeline.json"
    bare.write_bytes(_timeline_bytes(_visit(), _activity()))
    archive = open_archive(bare)
    assert [p.name for p in detect_sources(archive)] == ["timeline"]
    drafts = list(parse(archive, ParseContext()))
    assert len(drafts) == 2
    assert {d.meta["type"] for d in drafts} == {"visit", "route"}


def test_parse_reads_both_formats_and_skips_decoys(tmp_path: Path) -> None:
    members = {
        "Timeline.json": _timeline_bytes(_visit()),
        "Takeout/Timeline/Timeline Edits.json": json.dumps(
            {"timelineEdits": [_position_edit()]}
        ).encode(),
        "Takeout/Timeline/Settings.json": b'{"deviceSettings": []}',
        "Takeout/Timeline/Encrypted Backups.txt": b"decoy",
        "Takeout/Location History/Records.json": b'{"locations": [{"latitudeE7": 1}]}',
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    assert [p.name for p in detect_sources(open_archive(archive))] == ["timeline"]
    drafts = list(parse(open_archive(archive), ParseContext()))
    assert {d.meta["type"] for d in drafts} == {"visit", "position"}
    assert len(drafts) == 2


def test_two_timeline_members_dedup_across_members(tmp_path: Path) -> None:
    """Two Timeline.json members in one archive are re-exports of the same
    on-device history: per-member counters give both copies identical
    external_ids, so the engine dedups them instead of double-importing."""
    body = _timeline_bytes(_visit(), _visit())
    members = {
        "old/Timeline.json": body,
        "new/Timeline.json": body,
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    drafts = list(parse(open_archive(archive), ParseContext()))
    assert len(drafts) == 4
    assert len({d.external_id for d in drafts}) == 2


def test_parse_empty_archive_yields_nothing(tmp_path: Path) -> None:
    archive = write_archive(tmp_path / "empty.zip", {"decoy/readme.md": b"x"}, "zip")
    assert list(parse(open_archive(archive), ParseContext())) == []


def test_bom_is_tolerated(tmp_path: Path) -> None:
    bare = tmp_path / "Timeline.json"
    bare.write_bytes(b"\xef\xbb\xbf" + _timeline_bytes(_visit()))
    drafts = list(parse(open_archive(bare), ParseContext()))
    assert len(drafts) == 1
