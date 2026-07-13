"""Deterministic synthetic Google Timeline generator (Android + Takeout).

Ships inside ``potluck.testing`` so tests, committed fixtures, and bench
scenarios share one deterministic source. Same arguments → identical bytes on
every machine, forever. Never put real personal data here — coordinates are
computed around a fictional (40.0x, -75.0x) grid, place ids are ``ChIJsynth*``
strings, labels are fixture names.

The member set mirrors the real 2025-12 exports (structure verified against
a real on-device export and Takeout part, shape only):

- ``Timeline.json`` at the ROOT — the bare Android on-device export (a plain
  file, not an archive: the SingleFileArchive import shape). Top-level key
  ORDER matches the real file: ``semanticSegments``, then ``rawSignals``
  (sensor telemetry the parser must skim over, never items), then
  ``userLocationProfile`` at the TAIL (the placeId→label join arrives after
  both big arrays — the reason the parser is two-pass). ``latLng`` renders
  exactly like the real export (``"40.0123456°, -75.0456789°"`` — degree
  signs, comma-space, negative longitudes exercised), timestamps as
  ISO-8601 with explicit offsets (``.000+05:30`` style, offsets cycling)
  plus the start/endTimeTimezoneUtcOffsetMinutes ints (absent on
  timelinePath segments, like the real file).
- ``Takeout/Timeline/Timeline Edits.json`` — the modern Takeout side:
  ``timelineEdits`` mixing rawSignal positions (E7 integer coordinates,
  ``Z``-suffixed timestamps — a DIFFERENT coordinate/timestamp rendering
  than Timeline.json, matching reality) with activityRecord/wifiScan
  telemetry and inferred/userEdited semantic-segment edits the parser skips.
- Detection-precision decoys: ``Takeout/Timeline/Settings.json``,
  ``Takeout/Timeline/Encrypted Backups.txt``, Fit's
  ``derived_com.google.location.sample…json``, News'
  ``followed_locations.txt``, and an old-era ``Location History/Records.json``
  (the pre-2025 format is a non-goal) — none may ever match or import.

Segment shapes are modular rules of the segment index ``i`` (not RNG draws),
so expected parser outcomes have exact closed forms. Per segment ``i`` (first
rule wins):

- ``i % 19 == 18`` (i > 0) → verbatim copy of segment ``i-1`` — same bytes.
  Exercises the parser's defensive ``#N`` identity suffixes
  (:func:`expected_duplicate_suffix_count`).
- ``i % 12 == 3``  → timelineMemory (trip summary, placeId-only — skipped).
- ``i % 4 == 2``   → timelinePath (GPS breadcrumbs — skipped, P5 fodder).
- ``i % 4 == 1``   → activity → one route item (start/end latLng,
  distanceMeters int-valued or fractional by ``i``).
- otherwise        → visit → one visit item; ``hierarchyLevel`` 1 at
  ``i % 8 == 7`` (nested child visits, kept like their level-0 peers),
  else 0. Visits cycle 6 place ids; ids 0–3 appear in the tail profile and
  ids 0–1 carry labels ("Synth Home" / "Synth Work") — so labeled, known-
  but-unlabeled, and unknown placeId joins are all exercised.

Edits shapes are modular rules of the edit index ``j``: ``j % 5 in {0, 3}``
→ a position record (one item, :func:`expected_position_count`); ``1``/``2``
→ activityRecord/wifiScan telemetry (skipped); ``4`` → an
inferredSemanticSegment edit (userEditedSemanticSegment at ``j % 10 == 9``;
both skipped).

Regenerate the golden fixture::

    python -c "
    from pathlib import Path
    from potluck.testing.timeline import write_timeline_export
    write_timeline_export(Path('tests/fixtures/timeline'), 48, seed=13, fmt='dir')
    "
"""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from potluck.testing.archives import write_archive

_BASE_TS = datetime(2024, 3, 1, 8, 0, 0, tzinfo=UTC)

# Timeline.json offsets cycle like a travelling user's real file; the ints
# are the start/endTimeTimezoneUtcOffsetMinutes values.
_OFFSET_MINUTES = (330, -480, 0)

_SEMANTIC_TYPES = ("UNKNOWN", "HOME", "WORK", "INFERRED_HOME", "SEARCHED_ADDRESS")
_ACTIVITY_TYPES = ("WALKING", "IN_PASSENGER_VEHICLE", "CYCLING", "IN_BUS", "UNKNOWN_ACTIVITY_TYPE")
_POSITION_SOURCES = ("GPS", "WIFI", "CELL")

_PLACE_COUNT = 6  # visits cycle these; 0-3 sit in the profile, 0-1 labeled
_PLACE_LABELS = {0: "Synth Home", 1: "Synth Work"}
_DEVICE_ID = "-4242424242424242424"  # numeric string, like the real export

_DECOYS: dict[str, bytes] = {
    "Takeout/Timeline/Settings.json": (
        b'{"deviceSettings": [{"deviceTag": 42, "platform": "SYNTHETIC"}]}'
    ),
    "Takeout/Timeline/Encrypted Backups.txt": b"Synthetic decoy: encrypted backups are off.\n",
    "Takeout/Fit/All Data/derived_com.google.location.sample_com.google..json": (
        b'{"Data Source": "synthetic-decoy", "Data Points": []}'
    ),
    "Takeout/News/followed_locations.txt": b"Synthetic Locality\n",
    # The discontinued pre-2025 format (non-goal) must never match detection.
    "Takeout/Location History/Records.json": b'{"locations": []}',
}


def _shape(i: int) -> str:
    """The modular shape rule for segment *i* (module docstring)."""
    if i > 0 and i % 19 == 18:
        return "dup"
    if i % 12 == 3:
        return "memory"
    if i % 4 == 2:
        return "path"
    if i % 4 == 1:
        return "activity"
    return "visit"


def _effective_shape(i: int) -> str:
    """The shape a segment renders as (a dup copies its predecessor)."""
    return _shape(i - 1) if _shape(i) == "dup" else _shape(i)


def expected_visit_count(count: int) -> int:
    """Visit items the parser yields for one generated Timeline.json
    (duplicates import via ``#N`` suffixes; paths/memories never do)."""
    return sum(1 for i in range(count) if _effective_shape(i) == "visit")


def expected_route_count(count: int) -> int:
    """Route items the parser yields for one generated Timeline.json."""
    return sum(1 for i in range(count) if _effective_shape(i) == "activity")


def expected_duplicate_suffix_count(count: int) -> int:
    """Segments that import with a ``#N`` external-id suffix (verbatim dups
    of an item-producing segment)."""
    return sum(
        1
        for i in range(count)
        if _shape(i) == "dup" and _effective_shape(i) in ("visit", "activity")
    )


def expected_position_count(count: int) -> int:
    """Position items the parser yields for one generated Timeline Edits.json
    of *count* edits (telemetry and semantic-segment edits are skipped)."""
    return sum(1 for j in range(count) if j % 5 in (0, 3))


def segment_start(i: int) -> datetime:
    """The aware start instant of segment *i* (45 min apart, 30 min long)."""
    return _BASE_TS + timedelta(minutes=45 * i)


def _instant(dt: datetime, offset_minutes: int) -> str:
    """Render *dt* exactly like Timeline.json: millisecond fraction, explicit
    offset (never Z), in the local time of *offset_minutes*."""
    local = dt.astimezone(timezone(timedelta(minutes=offset_minutes)))
    sign = "+" if offset_minutes >= 0 else "-"
    hh, mm = divmod(abs(offset_minutes), 60)
    return f"{local:%Y-%m-%dT%H:%M:%S}.000{sign}{hh:02d}:{mm:02d}"


def _lat_lng(salt: int, k: int) -> str:
    """One degree-sign coordinate pair on the fictional grid (negative
    longitude — sign parsing is always exercised)."""
    lat = 40.0 + ((salt + k * 37) % 500) / 10000
    lon = -75.0 - ((salt + k * 53) % 500) / 10000
    return f"{lat:.7f}°, {lon:.7f}°"


def _place_id(pi: int) -> str:
    return f"ChIJsynthetic{pi:04d}"


def _segment(i: int, salt: int) -> dict[str, object]:
    """The exported segment for logical index *i* (never called for dups),
    field names and order exactly as the real 2025-12 export."""
    shape = _shape(i)
    off = _OFFSET_MINUTES[i % len(_OFFSET_MINUTES)]
    start, end = segment_start(i), segment_start(i) + timedelta(minutes=30)
    segment: dict[str, object] = {
        "startTime": _instant(start, off),
        "endTime": _instant(end, off),
    }
    if shape != "path":
        segment["startTimeTimezoneUtcOffsetMinutes"] = off
        segment["endTimeTimezoneUtcOffsetMinutes"] = off

    if shape == "path":
        segment["timelinePath"] = [
            {
                "point": _lat_lng(salt, i * 10 + step),
                "time": _instant(start + timedelta(minutes=step), off),
            }
            for step in range(3)
        ]
    elif shape == "memory":
        segment["timelineMemory"] = {
            "trip": {
                "distanceFromOriginKms": 20 + i % 400,
                "destinations": [{"identifier": {"placeId": _place_id(i % _PLACE_COUNT)}}],
            }
        }
    elif shape == "activity":
        segment["activity"] = {
            "start": {"latLng": _lat_lng(salt, i)},
            "end": {"latLng": _lat_lng(salt, i + 1)},
            "distanceMeters": 250 + (salt + i * 97) % 12000 + (i % 4) / 4,
            "probability": ((salt + i * 7) % 50 + 50) / 100,
            "topCandidate": {
                "type": _ACTIVITY_TYPES[i % len(_ACTIVITY_TYPES)],
                "probability": ((salt + i * 11) % 50 + 50) / 100,
            },
        }
    else:  # visit
        segment["visit"] = {
            "hierarchyLevel": 1 if i % 8 == 7 else 0,
            "probability": ((salt + i * 7) % 60 + 40) / 100,
            "topCandidate": {
                "placeId": _place_id(i % _PLACE_COUNT),
                "semanticType": _SEMANTIC_TYPES[i % len(_SEMANTIC_TYPES)],
                "probability": ((salt + i * 11) % 60 + 40) / 100,
                "placeLocation": {"latLng": _lat_lng(salt, i % _PLACE_COUNT)},
            },
        }
    return segment


def _raw_signal(k: int, salt: int) -> dict[str, object]:
    """One rawSignals telemetry entry (skim-over decoys; note the DIFFERENT
    position rendering than Timeline Edits: LatLng string, accuracyMeters)."""
    ts = _instant(_BASE_TS + timedelta(minutes=7 * k), _OFFSET_MINUTES[k % 3])
    if k % 3 == 0:
        return {
            "position": {
                "LatLng": _lat_lng(salt, 900 + k),
                "accuracyMeters": 10 + k % 90,
                "altitudeMeters": 100.5 + k,
                "source": _POSITION_SOURCES[k % 3],
                "timestamp": ts,
                "speedMetersPerSecond": (k % 40) / 4,
            }
        }
    if k % 3 == 1:
        return {"activityRecord": {"probableActivities": [], "timestamp": ts}}
    return {"wifiScan": {"deliveryTime": ts, "devicesRecords": []}}


def timeline_json_bytes(count: int, seed: int = 42) -> bytes:
    """The bare Android ``Timeline.json``: segments, telemetry, tail profile.

    Key order matters and matches the real file — ``userLocationProfile``
    LAST, after both arrays (the parser's two-pass reason). Segments are
    rendered one at a time so a bench-scale corpus never builds one giant
    tree (the chrome generator's posture).
    """
    salt = seed * 1009
    segments = ",\n".join(
        json.dumps(_segment(i - 1 if _shape(i) == "dup" else i, salt), ensure_ascii=False)
        for i in range(count)
    )
    raw_signals = ",\n".join(
        json.dumps(_raw_signal(k, salt), ensure_ascii=False) for k in range(min(count, 12))
    )
    profile = json.dumps(
        {
            "frequentPlaces": [
                {
                    "placeId": _place_id(pi),
                    "placeLocation": _lat_lng(salt, pi),
                    **({"label": _PLACE_LABELS[pi]} if pi in _PLACE_LABELS else {}),
                }
                for pi in range(4)
            ]
        },
        ensure_ascii=False,
    )
    return (
        '{"semanticSegments": [\n' + segments + '\n],\n"rawSignals": [\n' + raw_signals + "\n],\n"
        '"userLocationProfile": ' + profile + "}\n"
    ).encode("utf-8")


def _edit(j: int, salt: int) -> dict[str, object]:
    """One timelineEdits entry (module docstring rules)."""
    ts = f"{_BASE_TS + timedelta(minutes=11 * j):%Y-%m-%dT%H:%M:%S}.000Z"
    edit: dict[str, object] = {"deviceId": _DEVICE_ID}
    if j % 5 in (0, 3):
        lat_e7 = 400000000 + ((salt + j * 37) % 500) * 1000
        lng_e7 = -750000000 - ((salt + j * 53) % 500) * 1000
        edit["rawSignal"] = {
            "signal": {
                "position": {
                    "point": {"latE7": lat_e7, "lngE7": lng_e7},
                    "accuracyMm": 8000 + (j % 30) * 500,
                    "altitudeMeters": 90.25 + j,
                    "source": _POSITION_SOURCES[j % 3],
                    "timestamp": ts,
                    "speedMetersPerSecond": (j % 32) / 8,
                }
            },
            "additionalTimestamp": ts,
        }
    elif j % 5 == 1:
        edit["rawSignal"] = {
            "signal": {"activityRecord": {"probableActivities": [], "timestamp": ts}},
            "additionalTimestamp": ts,
        }
    elif j % 5 == 2:
        edit["rawSignal"] = {"signal": {"wifiScan": {"deliveryTime": ts, "devicesRecords": []}}}
    else:
        key = "userEditedSemanticSegment" if j % 10 == 9 else "inferredSemanticSegment"
        edit[key] = {
            "startTime": _instant(_BASE_TS + timedelta(minutes=11 * j), 0),
            "endTime": _instant(_BASE_TS + timedelta(minutes=11 * j + 30), 0),
            "startTimeTimezoneUtcOffsetMinutes": 0,
            "endTimeTimezoneUtcOffsetMinutes": 0,
            "segment": {
                "visit": {
                    "hierarchyLevel": 0,
                    "probability": 0.75,
                    "topCandidate": {
                        "placeId": _place_id(j % _PLACE_COUNT),
                        "placeLocation": {"latE7": 400100000, "lngE7": -750100000},
                        "probability": 0.75,
                        "semanticType": "UNKNOWN",
                    },
                }
            },
        }
    return edit


def timeline_edits_bytes(count: int, seed: int = 42) -> bytes:
    """The Takeout ``Timeline Edits.json``: positions among skipped edits."""
    salt = seed * 1009
    edits = ",\n".join(json.dumps(_edit(j, salt), ensure_ascii=False) for j in range(count))
    return ('{"timelineEdits": [\n' + edits + "\n]}\n").encode("utf-8")


def timeline_members(count: int, seed: int = 42) -> dict[str, bytes]:
    """The member set of one synthetic export ({posix_name: content}): the
    bare root Timeline.json, the Takeout Edits file, and the decoys."""
    return {
        "Timeline.json": timeline_json_bytes(count, seed),
        "Takeout/Timeline/Timeline Edits.json": timeline_edits_bytes(count, seed),
        **_DECOYS,
    }


def write_timeline_export(
    dest_dir: Path,
    count: int,
    seed: int = 42,
    *,
    fmt: Literal["zip", "tgz", "dir"] = "zip",
) -> Path:
    """Materialise a synthetic Timeline export in *dest_dir*.

    Returns the archive path (or the directory root for ``fmt="dir"`` —
    whose root-level ``Timeline.json`` doubles as the bare-file fixture).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    members = timeline_members(count, seed)
    if fmt == "dir":
        dest = dest_dir / "timeline-synth-001"
        write_archive(dest, members, "dir")
        return dest
    ext = "zip" if fmt == "zip" else "tgz"
    dest = dest_dir / f"timeline-synth-001.{ext}"
    write_archive(dest, members, fmt)
    return dest
