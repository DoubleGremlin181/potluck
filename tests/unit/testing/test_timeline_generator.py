"""Deterministic Timeline generator (potluck.testing.timeline)."""

import json
import re
from pathlib import Path
from typing import Any

from potluck.testing.timeline import (
    expected_duplicate_suffix_count,
    expected_position_count,
    expected_route_count,
    expected_visit_count,
    timeline_members,
    write_timeline_export,
)

_TIMELINE = "Timeline.json"
_EDITS = "Takeout/Timeline/Timeline Edits.json"

_LATLNG_RE = re.compile(r"^-?\d+\.\d{7}°, -?\d+\.\d{7}°$")
_OFFSET_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.000[+-]\d{2}:\d{2}$")


def _segments(members: dict[str, bytes]) -> list[dict[str, Any]]:
    doc = json.loads(members[_TIMELINE])
    assert isinstance(doc, dict)
    segments = doc["semanticSegments"]
    assert isinstance(segments, list)
    return segments


def test_same_arguments_produce_identical_bytes() -> None:
    assert timeline_members(24, seed=5) == timeline_members(24, seed=5)


def test_different_seeds_produce_different_timelines() -> None:
    assert timeline_members(24, seed=5)[_TIMELINE] != timeline_members(24, seed=6)[_TIMELINE]


def test_member_set_mirrors_real_export_shape() -> None:
    """Root Timeline.json (the bare Android file), the Takeout Edits file,
    and every detection-precision decoy of the real exports."""
    members = timeline_members(4)
    assert sorted(members) == [
        "Takeout/Fit/All Data/derived_com.google.location.sample_com.google..json",
        "Takeout/Location History/Records.json",
        "Takeout/News/followed_locations.txt",
        "Takeout/Timeline/Encrypted Backups.txt",
        "Takeout/Timeline/Settings.json",
        _EDITS,
        _TIMELINE,
    ]
    doc = json.loads(members[_TIMELINE])
    # The real on-device export's top-level keys, in its order — the tail
    # profile position is the reason the parser is two-pass.
    assert list(doc) == ["semanticSegments", "rawSignals", "userLocationProfile"]


def test_profile_carries_labeled_and_unlabeled_places() -> None:
    doc = json.loads(timeline_members(12)[_TIMELINE])
    places = doc["userLocationProfile"]["frequentPlaces"]
    labels = [p.get("label") for p in places]
    assert len(places) == 4
    assert labels == ["Synth Home", "Synth Work", None, None]


def test_segments_match_closed_forms() -> None:
    count = 48
    segments = _segments(timeline_members(count, seed=7))
    assert len(segments) == count
    visits = [s for s in segments if "visit" in s]
    routes = [s for s in segments if "activity" in s]
    assert len(visits) == expected_visit_count(count)
    assert len(routes) == expected_route_count(count)

    dup_indices = [i for i in range(count) if i > 0 and i % 19 == 18]
    assert expected_duplicate_suffix_count(count) <= len(dup_indices)
    for i in dup_indices:
        assert segments[i] == segments[i - 1]  # verbatim copies (identity input)

    # Both skip flavors are always present at this size.
    assert any("timelinePath" in s for s in segments)
    assert any("timelineMemory" in s for s in segments)
    # Nested child visits appear alongside level-0 ones.
    assert {s["visit"]["hierarchyLevel"] for s in visits} == {0, 1}


def test_timeline_byte_shapes_match_real_export() -> None:
    """Degree-sign latLng with negative longitudes, offset timestamps, tz
    offset ints everywhere except timelinePath segments."""
    segments = _segments(timeline_members(30, seed=7))
    for s in segments:
        assert _OFFSET_TS_RE.match(str(s["startTime"])), s["startTime"]
        assert ("startTimeTimezoneUtcOffsetMinutes" in s) == ("timelinePath" not in s)
        if "visit" in s:
            lat_lng = s["visit"]["topCandidate"]["placeLocation"]["latLng"]
            assert _LATLNG_RE.match(lat_lng), lat_lng
            assert "°, -" in lat_lng  # negative longitude exercised
        if "activity" in s:
            assert _LATLNG_RE.match(s["activity"]["start"]["latLng"])
            assert _LATLNG_RE.match(s["activity"]["end"]["latLng"])
    offsets = {str(s["startTime"])[-6:] for s in segments}
    assert len(offsets) > 1  # offsets vary like a travelling user's file


def test_edits_match_closed_forms_and_e7_shape() -> None:
    count = 40
    doc = json.loads(timeline_members(count, seed=7)[_EDITS])
    edits = doc["timelineEdits"]
    assert len(edits) == count
    positions = [
        e
        for e in edits
        if isinstance(e.get("rawSignal"), dict) and "position" in e["rawSignal"]["signal"]
    ]
    assert len(positions) == expected_position_count(count)
    point = positions[0]["rawSignal"]["signal"]["position"]["point"]
    assert isinstance(point["latE7"], int)
    assert point["lngE7"] < 0  # negative longitude exercised on the E7 side too
    # The skipped edit kinds are all present at this size.
    kinds = {k for e in edits for k in e if k != "deviceId"}
    assert kinds == {"rawSignal", "inferredSemanticSegment", "userEditedSemanticSegment"}
    signals = {k for e in edits if "rawSignal" in e for k in e["rawSignal"]["signal"]}
    assert signals == {"position", "activityRecord", "wifiScan"}


def test_closed_forms_are_pure_ints() -> None:
    assert expected_visit_count(0) == 0
    assert expected_route_count(0) == 0
    assert expected_duplicate_suffix_count(0) == 0
    assert expected_position_count(0) == 0
    assert expected_visit_count(48) + expected_route_count(48) <= 48


def test_write_export_dir_layout(tmp_path: Path) -> None:
    root = write_timeline_export(tmp_path, 6, seed=3, fmt="dir")
    assert root.name == "timeline-synth-001"
    assert (root / "Timeline.json").is_file()
    assert (root / "Takeout" / "Timeline" / "Timeline Edits.json").is_file()
    assert (root / "Takeout" / "Timeline" / "Settings.json").is_file()


def test_write_export_zip_naming(tmp_path: Path) -> None:
    archive = write_timeline_export(tmp_path, 6, seed=3)
    assert archive.name == "timeline-synth-001.zip"
