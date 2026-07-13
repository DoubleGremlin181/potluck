"""Google Timeline source plugin: Android Timeline.json + Takeout Timeline Edits.

Two formats, one plugin (both are the same product's location history;
format spec v1 authoritative, every claim below verified against a real
2025-12 on-device export and Takeout part, shape only; v0 read both files
and its knowledge ports: the degree-string coordinate parsing, the E7
integer fallback layouts, the semantic-type place naming):

- **Android on-device export**: a BARE ``Timeline.json`` (~93 MB real, not
  an archive — the SingleFileArchive import shape). Top level:
  ``semanticSegments`` (48,749 real), ``rawSignals`` (40,960), and
  ``userLocationProfile`` at the TAIL. Segment flavors by key signature:
  ``visit`` (14,349), ``activity`` (15,018), ``timelinePath`` (19,313),
  ``timelineMemory`` (69). Coordinates render as ``"12.3456789°,
  98.7654321°"`` (degree signs, comma-space, negative values real);
  timestamps are ISO-8601 with explicit offsets (never Z) plus
  start/endTimeTimezoneUtcOffsetMinutes ints (absent on timelinePath).
- **Modern Takeout side**: ``Takeout/Timeline/Timeline Edits.json`` —
  ``timelineEdits`` (822 real) of ``{deviceId, rawSignal | inferred… |
  userEdited…}``. Only 200 of the real 822 are rawSignal POSITIONS (E7
  integer coordinates under ``signal.position.point``, ``Z``-suffixed
  timestamps); the rest are activityRecord (399) / wifiScan (218) telemetry
  and 5 semantic-segment edits. The old ``Location History`` folder
  (Records.json / Semantic Location History) is a NON-GOAL: Google
  discontinued it (Dec 2024) and moved the data on-device.

Kind mapping: the issue predates the locked 12-kind vocabulary; "location
visits, places" map to ``kind=location`` with a ``meta.type`` discriminator
(the Reddit meta.type resolution) — ``visit`` (place stays), ``route``
(activity segments: movement between places), ``position`` (Timeline Edits
raw fixes, v0 parity — the only Takeout-side location signal). Satellite:
the locations table (migration 013) carries lat/lon (start point for
routes), end_lat/end_lon (route end), place_id, semantic_type (visit place
type or route activity type, verbatim), distance_m.

Skipped as items, all documented: ``rawSignals`` in Timeline.json (41k rows
of sensor telemetry; semanticSegments are their distillation), the
``timelinePath`` flavor (19k segments of raw GPS breadcrumbs with zero
text, overlapping visits/routes in time — P5-enrich fodder, not items),
``timelineMemory`` (69 real; trip summaries carrying placeIds but no
coordinates), Timeline Edits' activityRecord/wifiScan telemetry and
inferred/userEditedSemanticSegment entries (visit/activity data the
on-device file covers far better), Settings.json and Encrypted Backups.txt.

hierarchyLevel: kept at EVERY level, with the level in meta (filterable).
Real evidence: 13,080 level-0 + 1,269 level-1 visits; all 477 exact-span
pairs are (level-0, level-1) parent/child refinements and only 15 of them
name the same place — level-1 visits reference 585 distinct places of which
only 64 ever appear at level 0, so dropping them would lose real places.

FTS text: title only (coordinates are satellite data, not prose; text stays
None). Visits: the tail profile's placeId→label join ("Home"-style user
labels) wins, else the humanized semanticType (v0's
``_semantic_type_to_place_name`` map, extended to the real 2025 enum, with
a mechanical Title-case fallback for future values). Routes: humanized
activity type + distance ("Walking — 1.2 km"). Positions: "Position".
ts = startTime (position timestamp for Edits); meta.end = endTime as UTC
ISO (the calendar posture); the tz-offset ints stay out of meta (derivable
from the offset already inside ts/end).

Identity policy (no native segment ids): ``timeline:<flavor>:<sha256[:16]>``
over VERBATIM exported strings, ``\\x1f``-separated — never parsed/cleaned
values, so parser evolution cannot re-mint identities (P2 finding 6).
Visit: raw startTime, endTime, latLng string, placeId. Route: raw
startTime, endTime, start latLng, end latLng. Position: verbatim deviceId,
both timestamps, latE7/lngE7. semanticType/probability/hierarchyLevel are
deliberately OUTSIDE the hash: Google re-infers them between exports
(UNKNOWN→HOME once the user confirms a place), and a re-inference must
UPDATE the item in place (satellite fields are hash-covered) rather than
mint a duplicate. Defensive first-seen ``#N`` suffixes cover byte-identical
segments, with occurrence counters scoped PER MEMBER: two Timeline.json
members in one archive are re-exports of the same on-device history, so
their copies must collide on external_id and dedup (the chrome/ynab
posture).

Memory posture: the 93 MB file is NEVER ``json.load``-ed (v0 did; the
issue's RSS gate exists to kill that). A hand-rolled incremental walk
(chrome's raw_decode pattern) holds the decoded text plus ONE segment; the
placeId→label profile sits at the file's tail, AFTER both big arrays, so
parse() makes two passes per member (the google_chat two-pass precedent):
pass 1 skims to the profile — discarding the arrays ELEMENT-WISE, never as
trees — pass 2 streams semanticSegments and yields. JSON errors mid-member
log one WARNING and stop that member (drafts already yielded stand); a top
level without the expected array logs one WARNING and yields nothing; an
empty array is a legitimate state and stays silent.

Detection: the bare file's fixed basename is the anchor — ``Timeline.json``
at any depth (the root alternative is what SingleFileArchive's basename
member matches; nested matches cover hand-organized folders; no real
Takeout tree contains a colliding ``Timeline.json``, and a foreign shape
under that name warns rather than importing silently). The Edits file is
anchored on its ``Timeline/`` parent segment, so Settings.json, Encrypted
Backups.txt, Fit's location samples, News' followed_locations.txt, and the
old-era ``Location History/…`` paths never match. Consequence: a lone
hand-extracted ``Timeline Edits.json`` without its Timeline/ parent is
deliberately not detected.
"""

import hashlib
import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from pydantic import JsonValue

from potluck.ingest.identity import occurrence_suffix
from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import LocationDraft
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)

# The bare on-device file by its fixed basename; the Edits file anchored on
# its Timeline/ parent. '*/' ('*' crosses '/') covers Takeout/ nesting and
# re-zipped deeper layouts, the bare alternatives the root-relative shapes
# (SingleFileArchive exposes a lone file AS its basename).
_TIMELINE_GLOB = Glob("Timeline.json|*/Timeline.json")
_EDITS_GLOB = Glob("Timeline/Timeline Edits.json|*/Timeline/Timeline Edits.json")
_EXPORT_GLOB = Glob(f"{_TIMELINE_GLOB.pattern}|{_EDITS_GLOB.pattern}")

# The real coordinate rendering: signed decimal degrees with a degree sign,
# comma-space separated. Anchored end to end — junk affixes must fail, never
# parse into something (v0's loose regex accepted garbage prefixes).
_LATLNG_RE: Final = re.compile(r"^\s*(-?\d+(?:\.\d+)?)°, ?(-?\d+(?:\.\d+)?)°\s*$")

# 16 hex chars = 64 bits of fingerprint — the chrome/identity sizing.
_DIGEST_CHARS: Final = 16

# v0's _semantic_type_to_place_name, extended to the real 2025 enum
# (distribution in the module docstring). Unlisted values fall back to
# mechanical humanization so future enum growth degrades readably.
_PLACE_NAMES: Final = {
    "HOME": "Home",
    "INFERRED_HOME": "Home (inferred)",
    "WORK": "Work",
    "INFERRED_WORK": "Work (inferred)",
    "SCHOOL": "School",
    "GYM": "Gym",
    "ALIASED_LOCATION": "Saved place",
    "SEARCHED_ADDRESS": "Searched address",
    "UNKNOWN": "Unknown place",
}
_UNKNOWN_PLACE: Final = "Unknown place"

_DECODER: Final = json.JSONDecoder()
_WS: Final = " \t\n\r"


@dataclass(slots=True)
class _MemberFlags:
    """Once-per-member warning latches (timestamp shape, unknown flavor)."""

    ts_warned: bool = False
    flavor_warned: bool = False


# ---------------------------------------------------------------------------
# Incremental JSON walk (chrome's raw_decode pattern; arrays skipped
# element-wise so the 41k-entry rawSignals never becomes one tree)
# ---------------------------------------------------------------------------


def _skip_ws(text: str, idx: int) -> int:
    while idx < len(text) and text[idx] in _WS:
        idx += 1
    return idx


def _iter_array(text: str, idx: int) -> Iterator[object]:
    """Yield the elements of the array starting at *idx* (which must point at
    ``[``), one at a time. Raises json.JSONDecodeError when malformed."""
    idx = _skip_ws(text, idx + 1)
    if idx < len(text) and text[idx] == "]":
        return
    while True:
        element, idx = _DECODER.raw_decode(text, idx)
        yield element
        idx = _skip_ws(text, idx)
        if idx < len(text) and text[idx] == ",":
            idx = _skip_ws(text, idx + 1)
            continue
        if idx < len(text) and text[idx] == "]":
            return
        raise json.JSONDecodeError("expected ',' or ']' in array", text, idx)


def _skip_value(text: str, idx: int) -> int:
    """Advance past the value at *idx*, returning the index just after it.

    Arrays are skipped ELEMENT-WISE (one decoded element at a time — flat
    memory even for the huge segment/signal arrays); anything else is
    raw_decode'd and dropped (tiny in practice: scalars and the profile).
    """
    if idx < len(text) and text[idx] == "[":
        idx = _skip_ws(text, idx + 1)
        if idx < len(text) and text[idx] == "]":
            return idx + 1
        while True:
            _, idx = _DECODER.raw_decode(text, idx)
            idx = _skip_ws(text, idx)
            if idx < len(text) and text[idx] == ",":
                idx = _skip_ws(text, idx + 1)
                continue
            if idx < len(text) and text[idx] == "]":
                return idx + 1
            raise json.JSONDecodeError("expected ',' or ']' in array", text, idx)
    _, idx = _DECODER.raw_decode(text, idx)
    return idx


def _walk_to_key(text: str, target: str) -> int | None:
    """Advance an incremental walk over the top-level object to *target*.

    Returns the index of the target's value, or None when the object ends
    without it; every earlier value is skipped flat via :func:`_skip_value`.
    Raises json.JSONDecodeError on malformed JSON (contained per member by
    the callers).
    """
    idx = _skip_ws(text, 0)
    if idx >= len(text) or text[idx] != "{":
        raise json.JSONDecodeError("expected a top-level object", text, idx)
    idx = _skip_ws(text, idx + 1)
    if idx < len(text) and text[idx] == "}":
        return None
    while True:
        key, idx = _DECODER.raw_decode(text, idx)
        idx = _skip_ws(text, idx)
        if idx >= len(text) or text[idx] != ":":
            raise json.JSONDecodeError("expected ':' after object key", text, idx)
        idx = _skip_ws(text, idx + 1)
        if key == target:
            return idx
        idx = _skip_ws(text, _skip_value(text, idx))
        if idx < len(text) and text[idx] == ",":
            idx = _skip_ws(text, idx + 1)
            continue
        if idx < len(text) and text[idx] == "}":
            return None
        raise json.JSONDecodeError("expected ',' or '}' in object", text, idx)


def _iter_top_array(text: str, key: str, member_name: str) -> Iterator[object]:
    """Yield the top-level *key* array's elements one at a time.

    Peak memory: the decoded text plus ONE element. A top level without the
    array logs one WARNING and yields nothing (a foreign shape must never
    import as zero items silently). Raises json.JSONDecodeError on malformed
    JSON (contained by the caller).
    """
    idx = _walk_to_key(text, key)
    if idx is None or idx >= len(text) or text[idx] != "[":
        _logger.warning('timeline: %r has no "%s" array — member skipped', member_name, key)
        return
    yield from _iter_array(text, idx)


# ---------------------------------------------------------------------------
# Field parsing
# ---------------------------------------------------------------------------


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_latlng(raw: object) -> tuple[float, float] | None:
    """The degree-sign coordinate string → (lat, lon), or None for any
    foreign shape or out-of-range value — garbage is rejected, never parsed
    into 0.0."""
    if not isinstance(raw, str):
        return None
    m = _LATLNG_RE.match(raw)
    if m is None:
        return None
    lat, lon = float(m[1]), float(m[2])
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


def _parse_e7(container: object) -> tuple[float, float] | None:
    """latE7/lngE7 integer ten-millionths → (lat, lon), or None.

    bool is an int subclass and still foreign; out-of-range values are
    rejected like the degree-string path.
    """
    if not isinstance(container, dict):
        return None
    lat_e7, lng_e7 = container.get("latE7"), container.get("lngE7")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (lat_e7, lng_e7)):
        return None
    assert isinstance(lat_e7, int) and isinstance(lng_e7, int)  # narrowed above
    lat, lon = lat_e7 / 10_000_000, lng_e7 / 10_000_000
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


def _parse_instant(raw: object) -> datetime | None:
    """ISO-8601 string → aware datetime, or None for any foreign shape.

    Real values always carry an explicit offset (Timeline.json) or ``Z``
    (Timeline Edits); a naive value is read as UTC — the whatsapp/gmail
    unknown-zone policy.
    """
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _humanize(token: str) -> str:
    """``IN_PASSENGER_VEHICLE`` → ``In passenger vehicle`` (mechanical:
    honest for enum values the explicit maps do not know)."""
    words = token.replace("_", " ").strip().lower()
    return words[:1].upper() + words[1:]


def _format_distance(meters: float) -> str:
    return f"{meters / 1000:.1f} km" if meters >= 1000 else f"{meters:.0f} m"


def _fingerprint(flavor: str, parts: tuple[str, ...]) -> str:
    """``timeline:<flavor>:<sha256[:16]>`` over \\x1f-joined VERBATIM
    exported values (module docstring: the identity policy)."""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8", errors="replace")).hexdigest()
    return f"timeline:{flavor}:{digest[:_DIGEST_CHARS]}"


def _base_meta(flavor: str, raw_end: str | None) -> dict[str, JsonValue]:
    """meta.type + meta.end (UTC ISO, the calendar posture). An absent or
    foreign endTime simply leaves meta.end out — the raw string is already
    inside the identity hash either way."""
    meta: dict[str, JsonValue] = {"type": flavor}
    end = _parse_instant(raw_end) if raw_end is not None else None
    if end is not None:
        meta["end"] = end.astimezone(UTC).isoformat()
    return meta


def _segment_ts(
    raw_start: str | None, flags: _MemberFlags, member_name: str, ordinal: int
) -> datetime | None:
    """startTime → aware instant; an unparseable one keeps the item undated
    (identity hashes the raw string) with one latched WARNING per member."""
    ts = _parse_instant(raw_start) if raw_start is not None else None
    if ts is None and not flags.ts_warned:
        _logger.warning(
            "timeline: segment %d in %r has no recognizable timestamp — stored "
            "without one (further occurrences in this member are counted silently)",
            ordinal,
            member_name,
        )
        flags.ts_warned = True
    return ts


# ---------------------------------------------------------------------------
# Segment flavors → drafts
# ---------------------------------------------------------------------------


def _build_visit(
    segment: dict[str, object],
    visit: dict[str, object],
    labels: dict[str, str],
    member_name: str,
    ordinal: int,
    counters: dict[str, int],
    flags: _MemberFlags,
) -> LocationDraft | None:
    raw_start = _str_or_none(segment.get("startTime"))
    raw_end = _str_or_none(segment.get("endTime"))
    candidate: object = visit.get("topCandidate")
    if not isinstance(candidate, dict):
        candidate = {}
    place_location: object = candidate.get("placeLocation")
    raw_latlng = place_location.get("latLng") if isinstance(place_location, dict) else None
    coords = _parse_latlng(raw_latlng)
    if coords is None:
        _logger.warning(
            "timeline: visit %d in %r has no usable coordinates — skipped",
            ordinal,
            member_name,
        )
        return None

    place_id = _str_or_none(candidate.get("placeId"))
    semantic_type = _str_or_none(candidate.get("semanticType"))
    title = labels.get(place_id) if place_id is not None else None
    if title is None:
        title = (
            _PLACE_NAMES.get(semantic_type, _humanize(semantic_type))
            if semantic_type is not None
            else _UNKNOWN_PLACE
        )

    meta = _base_meta("visit", raw_end)
    hierarchy = visit.get("hierarchyLevel")
    if isinstance(hierarchy, int) and not isinstance(hierarchy, bool):
        meta["hierarchy_level"] = hierarchy

    assert isinstance(raw_latlng, str)  # coords parsed from it above
    base = _fingerprint("visit", (raw_start or "", raw_end or "", raw_latlng, place_id or ""))
    return LocationDraft(
        external_id=base + occurrence_suffix(counters, base),
        ts=_segment_ts(raw_start, flags, member_name, ordinal),
        title=title,
        lat=coords[0],
        lon=coords[1],
        place_id=place_id,
        semantic_type=semantic_type,
        meta=meta,
    )


def _build_route(
    segment: dict[str, object],
    activity: dict[str, object],
    member_name: str,
    ordinal: int,
    counters: dict[str, int],
    flags: _MemberFlags,
) -> LocationDraft | None:
    raw_start = _str_or_none(segment.get("startTime"))
    raw_end = _str_or_none(segment.get("endTime"))
    start_point: object = activity.get("start")
    end_point: object = activity.get("end")
    raw_start_latlng = start_point.get("latLng") if isinstance(start_point, dict) else None
    raw_end_latlng = end_point.get("latLng") if isinstance(end_point, dict) else None
    coords = _parse_latlng(raw_start_latlng)
    if coords is None:
        _logger.warning(
            "timeline: route %d in %r has no usable coordinates — skipped",
            ordinal,
            member_name,
        )
        return None
    end_coords = _parse_latlng(raw_end_latlng)
    if end_coords is None and raw_end_latlng is not None:
        # The movement still happened — degrade to a start-only route.
        _logger.warning(
            "timeline: route %d in %r has unusable end coordinates — start kept",
            ordinal,
            member_name,
        )

    candidate: object = activity.get("topCandidate")
    activity_type = _str_or_none(candidate.get("type")) if isinstance(candidate, dict) else None
    raw_distance = activity.get("distanceMeters")
    distance: float | None = None
    if isinstance(raw_distance, (int, float)) and not isinstance(raw_distance, bool):
        distance = float(raw_distance)
    elif raw_distance is not None:
        _logger.warning(
            "timeline: route %d in %r has a non-numeric distance — dropped",
            ordinal,
            member_name,
        )

    name = _humanize(activity_type) if activity_type is not None else "Route"
    title = f"{name} — {_format_distance(distance)}" if distance is not None else name

    raw_start_str = raw_start_latlng if isinstance(raw_start_latlng, str) else ""
    raw_end_str = raw_end_latlng if isinstance(raw_end_latlng, str) else ""
    base = _fingerprint("route", (raw_start or "", raw_end or "", raw_start_str, raw_end_str))
    return LocationDraft(
        external_id=base + occurrence_suffix(counters, base),
        ts=_segment_ts(raw_start, flags, member_name, ordinal),
        title=title,
        lat=coords[0],
        lon=coords[1],
        end_lat=end_coords[0] if end_coords is not None else None,
        end_lon=end_coords[1] if end_coords is not None else None,
        semantic_type=activity_type,
        distance_m=distance,
        meta=_base_meta("route", raw_end),
    )


def _build_segment_draft(
    element: object,
    labels: dict[str, str],
    member_name: str,
    ordinal: int,
    counters: dict[str, int],
    flags: _MemberFlags,
) -> LocationDraft | None:
    """Dispatch one semanticSegments element; None = skipped (the documented
    non-item flavors are silent, malformed shapes warn)."""
    if not isinstance(element, dict):
        _logger.warning(
            "timeline: segment %d in %r is not an object — skipped", ordinal, member_name
        )
        return None
    visit: object = element.get("visit")
    if isinstance(visit, dict):
        return _build_visit(element, visit, labels, member_name, ordinal, counters, flags)
    activity: object = element.get("activity")
    if isinstance(activity, dict):
        return _build_route(element, activity, member_name, ordinal, counters, flags)
    if "timelinePath" in element or "timelineMemory" in element:
        return None  # documented non-items (module docstring), never warned
    if not flags.flavor_warned:
        _logger.warning(
            "timeline: segment %d in %r has an unknown flavor — skipped (further "
            "occurrences in this member are counted silently)",
            ordinal,
            member_name,
        )
        flags.flavor_warned = True
    return None


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


def _profile_labels(text: str, member_name: str) -> dict[str, str]:
    """Pass 1: skim to the tail userLocationProfile; return placeId→label.

    The big arrays before it are skipped element-wise (flat memory). A
    missing profile (or one without labeled places) degrades silently to an
    empty map — titles fall back to semantic types; malformed JSON warns
    (pass 2 owns the segment-level containment).
    """
    try:
        idx = _walk_to_key(text, "userLocationProfile")
        profile: object = _DECODER.raw_decode(text, idx)[0] if idx is not None else None
    except json.JSONDecodeError as exc:
        _logger.warning(
            "timeline: JSON error while skimming %r for the location profile: %s "
            "— place labels unavailable",
            member_name,
            exc,
        )
        return {}
    places: object = profile.get("frequentPlaces") if isinstance(profile, dict) else None
    labels: dict[str, str] = {}
    if isinstance(places, list):
        for place in places:
            if not isinstance(place, dict):
                continue
            place_id = _str_or_none(place.get("placeId"))
            label = _str_or_none(place.get("label"))
            if place_id is not None and label is not None:
                labels[place_id] = label
    return labels


def _parse_segments(text: str, member_name: str, labels: dict[str, str]) -> Iterator[LocationDraft]:
    """Pass 2: stream semanticSegments and yield drafts.

    Occurrence counters are member-scoped (module docstring: re-export
    copies must dedup). A JSON error mid-array logs one WARNING and stops
    the member; drafts already yielded stand.
    """
    counters: dict[str, int] = {}
    flags = _MemberFlags()
    ordinal = 0
    try:
        for element in _iter_top_array(text, "semanticSegments", member_name):
            ordinal += 1
            draft = _build_segment_draft(element, labels, member_name, ordinal, counters, flags)
            if draft is not None:
                yield draft
    except json.JSONDecodeError as exc:
        _logger.warning(
            "timeline: JSON error in %r: %s — remaining segments skipped", member_name, exc
        )


def _build_position_draft(
    edit: dict[str, object],
    position: dict[str, object],
    member_name: str,
    ordinal: int,
    counters: dict[str, int],
    flags: _MemberFlags,
) -> LocationDraft | None:
    raw_signal = edit["rawSignal"]
    assert isinstance(raw_signal, dict)  # dispatch guaranteed it
    point: object = position.get("point")
    # Older generations put latE7/lngE7 on position itself (v0 knowledge).
    coords_container = point if isinstance(point, dict) else position
    coords = _parse_e7(coords_container)
    if coords is None:
        _logger.warning(
            "timeline: position %d in %r has no usable coordinates — skipped",
            ordinal,
            member_name,
        )
        return None

    device_id = _str_or_none(edit.get("deviceId"))
    raw_ts = _str_or_none(position.get("timestamp"))
    raw_additional = _str_or_none(raw_signal.get("additionalTimestamp"))
    ts = _parse_instant(raw_ts) if raw_ts is not None else None
    if ts is None and not flags.ts_warned:
        _logger.warning(
            "timeline: position %d in %r has no recognizable timestamp — stored "
            "without one (further occurrences in this member are counted silently)",
            ordinal,
            member_name,
        )
        flags.ts_warned = True

    meta: dict[str, JsonValue] = {"type": "position"}
    src = _str_or_none(position.get("source"))
    if src is not None:
        meta["source"] = src
    if device_id is not None:
        meta["device_id"] = device_id

    lat_e7, lng_e7 = coords_container.get("latE7"), coords_container.get("lngE7")
    base = _fingerprint(
        "position",
        (device_id or "", raw_ts or "", raw_additional or "", str(lat_e7), str(lng_e7)),
    )
    return LocationDraft(
        external_id=base + occurrence_suffix(counters, base),
        ts=ts,
        title="Position",
        lat=coords[0],
        lon=coords[1],
        meta=meta,
    )


def _parse_edits(text: str, member_name: str) -> Iterator[LocationDraft]:
    """Yield position drafts from one Timeline Edits member.

    Telemetry signals (activityRecord/wifiScan) and semantic-segment edits
    (inferred/userEdited) are documented non-items and skip silently; a
    non-object edit warns. Same counter scoping and JSON containment as the
    segments pass.
    """
    counters: dict[str, int] = {}
    flags = _MemberFlags()
    ordinal = 0
    try:
        for element in _iter_top_array(text, "timelineEdits", member_name):
            ordinal += 1
            if not isinstance(element, dict):
                _logger.warning(
                    "timeline: edit %d in %r is not an object — skipped", ordinal, member_name
                )
                continue
            raw_signal: object = element.get("rawSignal")
            if not isinstance(raw_signal, dict):
                continue  # inferred/userEdited semantic edits: documented non-items
            signal: object = raw_signal.get("signal")
            position = signal.get("position") if isinstance(signal, dict) else None
            if not isinstance(position, dict):
                continue  # activityRecord/wifiScan telemetry: documented non-items
            draft = _build_position_draft(element, position, member_name, ordinal, counters, flags)
            if draft is not None:
                yield draft
    except json.JSONDecodeError as exc:
        _logger.warning(
            "timeline: JSON error in %r: %s — remaining edits skipped", member_name, exc
        )


@source(
    name="timeline",
    detect=_EXPORT_GLOB,
    kinds=(ItemKind.LOCATION,),
    parser_version=1,
)
def parse(archive: Archive, ctx: ParseContext) -> Iterator[LocationDraft]:
    """Yield LocationDrafts from every Timeline member.

    Timeline.json members take two streaming passes (pass 1 skims each to
    its tail placeId→label profile, pass 2 streams the segments) because the
    profile sits AFTER the big arrays in the real file — the alternative,
    buffering 48k segments until the labels arrive, is exactly the tree the
    RSS gate forbids. The Edits pass streams positions the same incremental
    way. Per member the peak memory is the decoded text plus one element.
    ctx is part of the plugin contract but unused: there is nothing to
    parallelize.
    """
    labels_by_member: dict[str, dict[str, str]] = {}
    for member, stream in archive.iter_members("*Timeline.json"):
        if _TIMELINE_GLOB.matches(member.name):
            text = stream.read().decode("utf-8-sig", errors="replace")
            labels_by_member[member.name] = _profile_labels(text, member.name)
    for member, stream in archive.iter_members("*Timeline.json"):
        if _TIMELINE_GLOB.matches(member.name):
            text = stream.read().decode("utf-8-sig", errors="replace")
            yield from _parse_segments(text, member.name, labels_by_member.get(member.name, {}))
    for member, stream in archive.iter_members("*Timeline Edits.json"):
        if _EDITS_GLOB.matches(member.name):
            text = stream.read().decode("utf-8-sig", errors="replace")
            yield from _parse_edits(text, member.name)
