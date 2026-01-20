"""Location History ingestion from Google Takeout and Android Timeline.

Handles:
- Timeline.json: Android Timeline export (rich data)
- Takeout/Timeline/Timeline Edits.json: Google Takeout timeline (sparse)
- Takeout/Maps/My labeled places/Labeled places.json: Named locations

The Android Timeline export contains rich semantic data including:
- Visit segments (places visited with duration)
- Activity segments (movement between places)
- Timeline path (raw GPS points)
"""

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.locations import (
    Location,
    LocationHistory,
    LocationType,
    LocationVisit,
)
from potluck.pipeline.dtos import PipelineFilter

logger = get_logger(__name__)

# Mapping from Google's semantic types to LocationType
SEMANTIC_TYPE_MAP: dict[str, LocationType] = {
    "HOME": LocationType.HOME,
    "WORK": LocationType.WORK,
    "SCHOOL": LocationType.SCHOOL,
    "GYM": LocationType.GYM,
    "RESTAURANT": LocationType.RESTAURANT,
    "STORE": LocationType.STORE,
    "TRANSIT": LocationType.TRANSIT,
    "AIRPORT": LocationType.AIRPORT,
    "HOTEL": LocationType.HOTEL,
    "ATTRACTION": LocationType.ATTRACTION,
    "UNKNOWN": LocationType.UNKNOWN,
}


def ingest_location_visits(
    path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[LocationVisit | LocationHistory | Location]:
    """Ingest location visits from Google Timeline data.

    Supports both Android Timeline export (rich) and Google Takeout data (sparse).

    Args:
        path: Path to the extracted takeout directory or Timeline.json.
        filters: Optional date range filters.

    Yields:
        LocationVisit, LocationHistory, and Location entities.
    """
    # Check for Android Timeline export first (richest data)
    timeline_file = path / "Timeline.json"
    if timeline_file.exists():
        logger.info("Processing Android Timeline export")
        yield from _process_android_timeline(timeline_file, filters)

    # Check for Google Takeout Timeline data
    takeout_timeline_dir = _find_timeline_dir(path)
    if takeout_timeline_dir:
        logger.info("Processing Google Takeout Timeline data")
        yield from _process_takeout_timeline(takeout_timeline_dir, filters)

    # Check for labeled places
    labeled_places_dir = _find_labeled_places_dir(path)
    if labeled_places_dir:
        logger.info("Processing labeled places")
        yield from _process_labeled_places(labeled_places_dir)


def _find_timeline_dir(path: Path) -> Path | None:
    """Find Timeline directory in Google Takeout."""
    candidates = [
        path / "Takeout" / "Timeline",
        path / "Takeout" / "Location History",
        path / "Timeline",
        path / "Location History",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _find_labeled_places_dir(path: Path) -> Path | None:
    """Find labeled places directory in Google Takeout."""
    candidates = [
        path / "Takeout" / "Maps" / "My labeled places",
        path / "Maps" / "My labeled places",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _process_android_timeline(
    timeline_file: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[LocationVisit | LocationHistory]:
    """Process Android Timeline.json export.

    Args:
        timeline_file: Path to Timeline.json.
        filters: Optional date range filters.

    Yields:
        LocationVisit and LocationHistory entities.
    """
    try:
        content = timeline_file.read_text(encoding="utf-8")
        data = json.loads(content)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to parse Timeline.json: {e}")
        return

    segments = data.get("semanticSegments", [])
    logger.debug(f"Processing {len(segments)} semantic segments")

    for segment in segments:
        start_time = _parse_iso_timestamp(segment.get("startTime"))
        if start_time is None:
            continue

        # Apply date filters
        if filters:
            if filters.since and start_time < filters.since:
                continue
            if filters.until and start_time >= filters.until:
                continue

        end_time = _parse_iso_timestamp(segment.get("endTime"))

        # Process visit segments
        if "visit" in segment:
            visit = _parse_visit_segment(segment, start_time, end_time)
            if visit:
                yield visit

        # Process activity segments as visits (travel between places)
        if "activity" in segment:
            activity_visit = _parse_activity_segment(segment, start_time, end_time)
            if activity_visit:
                yield activity_visit

        # Process timeline path (raw GPS points)
        if "timelinePath" in segment:
            yield from _parse_timeline_path(segment["timelinePath"], filters)


def _parse_visit_segment(
    segment: dict[str, Any],
    start_time: datetime,
    end_time: datetime | None,
) -> LocationVisit | None:
    """Parse a visit segment into a LocationVisit.

    Args:
        segment: The semantic segment containing visit data.
        start_time: Parsed start time.
        end_time: Parsed end time.

    Returns:
        LocationVisit entity or None if parsing fails.
    """
    visit = segment.get("visit", {})
    top_candidate = visit.get("topCandidate", {})

    # Get coordinates from place location
    place_location = top_candidate.get("placeLocation", {})
    lat_lng = place_location.get("latLng", "")
    lat, lng = _parse_lat_lng(lat_lng)

    if lat is None or lng is None:
        return None

    # Calculate duration
    duration_minutes = None
    if end_time:
        duration = (end_time - start_time).total_seconds() / 60
        duration_minutes = int(duration)

    # Get semantic type
    semantic_type = top_candidate.get("semanticType", "UNKNOWN")
    place_name = _semantic_type_to_place_name(semantic_type)

    return LocationVisit(
        source_type=SourceType.GOOGLE_TAKEOUT,
        latitude=lat,
        longitude=lng,
        started_at=start_time,
        ended_at=end_time,
        duration_minutes=duration_minutes,
        place_id=top_candidate.get("placeId"),
        place_name=place_name,
        confidence=top_candidate.get("probability"),
    )


def _parse_activity_segment(
    segment: dict[str, Any],
    start_time: datetime,
    end_time: datetime | None,
) -> LocationVisit | None:
    """Parse an activity segment into a LocationVisit.

    Activity segments represent travel between places.

    Args:
        segment: The semantic segment containing activity data.
        start_time: Parsed start time.
        end_time: Parsed end time.

    Returns:
        LocationVisit entity or None if parsing fails.
    """
    activity = segment.get("activity", {})

    # Use start location for the visit
    start_loc = activity.get("start", {})
    lat_lng = start_loc.get("latLng", "")
    lat, lng = _parse_lat_lng(lat_lng)

    if lat is None or lng is None:
        return None

    # Get activity type
    top_candidate = activity.get("topCandidate", {})
    activity_type = top_candidate.get("type", "UNKNOWN")

    # Calculate duration
    duration_minutes = None
    if end_time:
        duration = (end_time - start_time).total_seconds() / 60
        duration_minutes = int(duration)

    return LocationVisit(
        source_type=SourceType.GOOGLE_TAKEOUT,
        latitude=lat,
        longitude=lng,
        started_at=start_time,
        ended_at=end_time,
        duration_minutes=duration_minutes,
        activity_type=activity_type,
        confidence=top_candidate.get("probability"),
    )


def _parse_timeline_path(
    timeline_path: list[dict[str, Any]],
    filters: PipelineFilter | None = None,
) -> Iterator[LocationHistory]:
    """Parse timeline path points into LocationHistory entities.

    Args:
        timeline_path: List of path point dictionaries.
        filters: Optional date range filters.

    Yields:
        LocationHistory entities.
    """
    for point in timeline_path:
        timestamp = _parse_iso_timestamp(point.get("time"))
        if timestamp is None:
            continue

        # Apply date filters
        if filters:
            if filters.since and timestamp < filters.since:
                continue
            if filters.until and timestamp >= filters.until:
                continue

        lat_lng = point.get("point", "")
        lat, lng = _parse_lat_lng(lat_lng)

        if lat is None or lng is None:
            continue

        yield LocationHistory(
            source_type=SourceType.GOOGLE_TAKEOUT,
            latitude=lat,
            longitude=lng,
            timestamp=timestamp,
        )


def _process_takeout_timeline(
    timeline_dir: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[LocationVisit]:
    """Process Google Takeout Timeline Edits.json (sparse data).

    Args:
        timeline_dir: Path to Timeline directory.
        filters: Optional date range filters.

    Yields:
        LocationVisit entities.
    """
    edits_file = timeline_dir / "Timeline Edits.json"
    if not edits_file.exists():
        return

    try:
        content = edits_file.read_text(encoding="utf-8")
        data = json.loads(content)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to parse Timeline Edits.json: {e}")
        return

    # The format uses latE7/lngE7 (coordinates * 10^7)
    edits = data.get("timelineEdits", [])

    for edit in edits:
        raw_signal = edit.get("rawSignal", {})
        signal = raw_signal.get("signal", {})
        position = signal.get("position", {})

        lat_e7 = position.get("latE7")
        lng_e7 = position.get("lngE7")

        if lat_e7 is None or lng_e7 is None:
            continue

        # Convert E7 coordinates
        lat = lat_e7 / 10_000_000
        lng = lng_e7 / 10_000_000

        # Get timestamp
        timestamp_str = position.get("timestamp")
        timestamp = _parse_iso_timestamp(timestamp_str)
        if timestamp is None:
            continue

        # Apply date filters
        if filters:
            if filters.since and timestamp < filters.since:
                continue
            if filters.until and timestamp >= filters.until:
                continue

        yield LocationVisit(
            source_type=SourceType.GOOGLE_TAKEOUT,
            latitude=lat,
            longitude=lng,
            started_at=timestamp,
            accuracy_meters=position.get("accuracyMm", 0) / 1000
            if position.get("accuracyMm")
            else None,
        )


def _process_labeled_places(
    labeled_places_dir: Path,
) -> Iterator[Location]:
    """Process labeled places GeoJSON file.

    Args:
        labeled_places_dir: Path to labeled places directory.

    Yields:
        Location entities.
    """
    geojson_file = labeled_places_dir / "Labeled places.json"
    if not geojson_file.exists():
        return

    try:
        content = geojson_file.read_text(encoding="utf-8")
        data = json.loads(content)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to parse Labeled places.json: {e}")
        return

    # GeoJSON format
    features = data.get("features", [])

    for feature in features:
        properties = feature.get("properties", {})
        geometry = feature.get("geometry", {})

        # Get coordinates [lng, lat] (GeoJSON order)
        coordinates = geometry.get("coordinates", [])
        if len(coordinates) < 2:
            continue

        lng, lat = coordinates[0], coordinates[1]

        # Get name and address
        name = properties.get("name")
        if not name:
            continue

        address = properties.get("address")

        # Determine location type from name
        location_type = _name_to_location_type(name)

        yield Location(
            source_type=SourceType.GOOGLE_TAKEOUT,
            name=name,
            location_type=location_type,
            latitude=lat,
            longitude=lng,
            address=address,
        )


def _parse_lat_lng(lat_lng_str: str) -> tuple[float | None, float | None]:
    """Parse coordinates from 'lat°, lng°' format.

    Args:
        lat_lng_str: String like '18.5672701°, 73.9168584°'

    Returns:
        Tuple of (latitude, longitude) or (None, None) if parsing fails.
    """
    if not lat_lng_str:
        return None, None

    # Match pattern like "18.5672701°, 73.9168584°"
    pattern = r"(-?\d+\.?\d*)\s*°?\s*,\s*(-?\d+\.?\d*)\s*°?"
    match = re.match(pattern, lat_lng_str)

    if match:
        try:
            lat = float(match.group(1))
            lng = float(match.group(2))
            return lat, lng
        except ValueError:
            return None, None

    return None, None


def _parse_iso_timestamp(timestamp_str: str | None) -> datetime | None:
    """Parse ISO timestamp with timezone.

    Args:
        timestamp_str: ISO 8601 timestamp string.

    Returns:
        datetime in UTC or None if parsing fails.
    """
    if not timestamp_str:
        return None

    try:
        # Handle timestamps like "2014-04-09T12:41:13.000-07:00"
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        return dt.astimezone(UTC)
    except ValueError:
        return None


def _semantic_type_to_place_name(semantic_type: str) -> str | None:
    """Convert semantic type to human-readable place name.

    Args:
        semantic_type: Google's semantic type string.

    Returns:
        Human-readable place name or None.
    """
    name_map = {
        "HOME": "Home",
        "WORK": "Work",
        "SCHOOL": "School",
        "GYM": "Gym",
    }
    return name_map.get(semantic_type)


def _name_to_location_type(name: str) -> LocationType:
    """Determine location type from place name.

    Args:
        name: Place name string.

    Returns:
        LocationType enum value.
    """
    name_lower = name.lower()

    if name_lower in ("home", "my home"):
        return LocationType.HOME
    if name_lower in ("work", "office", "my work"):
        return LocationType.WORK
    if "school" in name_lower or "university" in name_lower:
        return LocationType.SCHOOL
    if "gym" in name_lower or "fitness" in name_lower:
        return LocationType.GYM
    if "airport" in name_lower:
        return LocationType.AIRPORT
    if "hotel" in name_lower:
        return LocationType.HOTEL

    return LocationType.OTHER
