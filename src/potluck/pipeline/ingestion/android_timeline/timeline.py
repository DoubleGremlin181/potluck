"""Android Timeline.json parsing logic.

Handles the rich semantic data from Android Timeline export including:
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
from potluck.models.locations import LocationHistory, LocationVisit
from potluck.pipeline.dtos import PipelineFilter

logger = get_logger(__name__)


def ingest_android_timeline(
    timeline_file: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[LocationVisit | LocationHistory]:
    """Ingest location data from Android Timeline.json export.

    Args:
        timeline_file: Path to Timeline.json file.
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
        source_type=SourceType.ANDROID_TIMELINE,
        latitude=lat,
        longitude=lng,
        started_at=start_time,
        ended_at=end_time,
        duration_minutes=duration_minutes,
        occurred_at=start_time,
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
        source_type=SourceType.ANDROID_TIMELINE,
        latitude=lat,
        longitude=lng,
        started_at=start_time,
        ended_at=end_time,
        duration_minutes=duration_minutes,
        occurred_at=start_time,
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
            source_type=SourceType.ANDROID_TIMELINE,
            latitude=lat,
            longitude=lng,
            timestamp=timestamp,
            occurred_at=timestamp,
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
