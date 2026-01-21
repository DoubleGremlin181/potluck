"""Location History ingestion from Google Takeout.

Handles:
- Takeout/Timeline/Timeline Edits.json: Google Takeout timeline (sparse)
- Takeout/Maps/My labeled places/Labeled places.json: Named locations

Note: Android Timeline export (Timeline.json at root) is handled by
the separate AndroidTimelineStage ingester which provides richer data.
"""

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.locations import (
    Location,
    LocationType,
    LocationVisit,
)
from potluck.pipeline.dtos import PipelineFilter

logger = get_logger(__name__)


def ingest_location_visits(
    path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[LocationVisit | Location]:
    """Ingest location visits from Google Takeout Timeline data.

    Handles Google Takeout's Timeline Edits and labeled places.
    For Android Timeline export (Timeline.json), use AndroidTimelineStage.

    Args:
        path: Path to the extracted takeout directory.
        filters: Optional date range filters.

    Yields:
        LocationVisit and Location entities.
    """
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

        # Coordinates can be directly on position or nested under position.point
        point = position.get("point", position)
        lat_e7 = point.get("latE7")
        lng_e7 = point.get("lngE7")

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

        # Generate source_id and content_hash for deduplication
        # Visit is uniquely identified by timestamp + coordinates
        source_id = f"timeline-{timestamp.isoformat()}-{lat}-{lng}"
        hash_content = f"{timestamp.isoformat()}|{lat}|{lng}"
        content_hash = hashlib.sha256(hash_content.encode()).hexdigest()

        yield LocationVisit(
            source_type=SourceType.GOOGLE_TAKEOUT,
            source_id=source_id,
            content_hash=content_hash,
            latitude=lat,
            longitude=lng,
            started_at=timestamp,
            occurred_at=timestamp,  # For search consistency
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

        # Generate source_id and content_hash for deduplication
        # Location is uniquely identified by name + coordinates
        source_id = f"labeled-{name}-{lat}-{lng}"
        hash_content = f"{name}|{lat}|{lng}"
        content_hash = hashlib.sha256(hash_content.encode()).hexdigest()

        yield Location(
            source_type=SourceType.GOOGLE_TAKEOUT,
            source_id=source_id,
            content_hash=content_hash,
            name=name,
            location_type=location_type,
            latitude=lat,
            longitude=lng,
            address=address,
        )


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
