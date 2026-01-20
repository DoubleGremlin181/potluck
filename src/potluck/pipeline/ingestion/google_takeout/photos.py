"""Google Photos media ingestion from Google Takeout.

Handles:
- Google Photos/*: Photo and video files
- *.supplemental-metadata.json: Sidecar metadata files

Each media file in Google Photos has a corresponding JSON sidecar file
containing metadata like:
- photoTakenTime: Timestamp when photo was taken
- geoData: GPS coordinates
- description: User-provided description
- title: Original filename
"""

import json
import mimetypes
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from potluck.core.logging import get_logger
from potluck.models.base import EntityType, SourceType
from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.utils.hashing import compute_file_hash

logger = get_logger(__name__)

# Media file extensions supported by Google Photos
MEDIA_EXTENSIONS = {
    # Images
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".bmp",
    ".tiff",
    ".tif",
    ".raw",
    ".cr2",
    ".nef",
    ".arw",
    # Videos
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".m4v",
    ".3gp",
    ".mpg",
    ".mpeg",
}

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".bmp",
    ".tiff",
    ".tif",
    ".raw",
    ".cr2",
    ".nef",
    ".arw",
}

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".m4v",
    ".3gp",
    ".mpg",
    ".mpeg",
}


def ingest_media(
    path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[Media]:
    """Ingest Google Photos media from Google Takeout.

    Scans for media files and their corresponding metadata sidecar files.
    Creates Media entities with extracted metadata including timestamps
    and GPS coordinates.

    Args:
        path: Path to the extracted takeout directory.
        filters: Optional date range filters.

    Yields:
        Media entities.
    """
    photos_dir = _find_google_photos_dir(path)
    if not photos_dir:
        logger.debug("No Google Photos directory found")
        return

    logger.info(f"Processing Google Photos at {photos_dir}")

    # Process all media files
    for media_file in sorted(photos_dir.rglob("*")):
        if not media_file.is_file():
            continue

        # Check if this is a media file
        if media_file.suffix.lower() not in MEDIA_EXTENSIONS:
            continue

        # Skip metadata files
        if media_file.suffix.lower() == ".json":
            continue

        # Try to find and parse metadata
        metadata = _load_metadata(media_file)

        # Get occurred_at from metadata or file mtime
        occurred_at = _get_occurred_at(media_file, metadata)

        # Apply date filters
        if filters and occurred_at:
            if filters.since and occurred_at < filters.since:
                continue
            if filters.until and occurred_at >= filters.until:
                continue

        # Create media entity
        media = _create_media_entity(media_file, metadata, occurred_at, photos_dir)
        if media:
            yield media


def _find_google_photos_dir(path: Path) -> Path | None:
    """Find Google Photos directory in takeout."""
    candidates = [
        path / "Takeout" / "Google Photos",
        path / "Google Photos",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _load_metadata(media_file: Path) -> dict[str, Any] | None:
    """Load metadata from sidecar JSON file.

    Google Takeout creates a .supplemental-metadata.json file for each
    media file, or sometimes just appends .json to the filename.

    Args:
        media_file: Path to the media file.

    Returns:
        Parsed metadata dict or None if not found.
    """
    # Try both naming conventions
    metadata_candidates = [
        media_file.with_suffix(media_file.suffix + ".supplemental-metadata.json"),
        media_file.with_suffix(media_file.suffix + ".json"),
        # Sometimes the metadata file has the original name + .json
        media_file.parent / (media_file.name + ".json"),
    ]

    for metadata_path in metadata_candidates:
        if metadata_path.exists():
            try:
                content = metadata_path.read_text(encoding="utf-8")
                data: dict[str, Any] = json.loads(content)
                return data
            except (OSError, json.JSONDecodeError) as e:
                logger.debug(f"Failed to parse metadata {metadata_path}: {e}")
                continue

    return None


def _get_occurred_at(media_file: Path, metadata: dict[str, Any] | None) -> datetime | None:
    """Get the timestamp when the photo/video was taken.

    Priority:
    1. photoTakenTime from metadata (most accurate)
    2. creationTime from metadata
    3. File modification time (fallback)

    Args:
        media_file: Path to the media file.
        metadata: Parsed metadata dict or None.

    Returns:
        datetime in UTC or None.
    """
    if metadata:
        # Try photoTakenTime first (most accurate)
        photo_taken = metadata.get("photoTakenTime", {})
        timestamp = photo_taken.get("timestamp")
        if timestamp:
            try:
                return datetime.fromtimestamp(int(timestamp), tz=UTC)
            except (ValueError, OSError):
                pass

        # Try creationTime as fallback
        creation = metadata.get("creationTime", {})
        timestamp = creation.get("timestamp")
        if timestamp:
            try:
                return datetime.fromtimestamp(int(timestamp), tz=UTC)
            except (ValueError, OSError):
                pass

    # Fall back to file mtime
    try:
        mtime = media_file.stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=UTC)
    except OSError:
        return None


def _create_media_entity(
    media_file: Path,
    metadata: dict[str, Any] | None,
    occurred_at: datetime | None,
    photos_dir: Path | None = None,
) -> Media | None:
    """Create a Media entity from a file and its metadata.

    Args:
        media_file: Path to the media file.
        metadata: Parsed metadata dict or None.
        occurred_at: Timestamp when the media was created.

    Returns:
        Media entity or None if creation fails.
    """
    # Determine media type from extension
    suffix = media_file.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        media_type = MediaType.IMAGE
    elif suffix in VIDEO_EXTENSIONS:
        media_type = MediaType.VIDEO
    else:
        media_type = MediaType.OTHER

    # Get MIME type
    mime_type, _ = mimetypes.guess_type(str(media_file))

    # Get file size
    try:
        file_size = media_file.stat().st_size
    except OSError:
        file_size = None

    # Compute file hash for deduplication
    try:
        file_hash = compute_file_hash(media_file)
    except (OSError, ValueError):
        file_hash = None

    # Extract metadata fields
    title = None
    latitude = None
    longitude = None
    altitude = None
    album_name = None

    if metadata:
        title = metadata.get("title")

        # Extract geo data
        geo_data = metadata.get("geoData", {})
        if geo_data:
            lat = geo_data.get("latitude")
            lng = geo_data.get("longitude")
            # Google Photos uses 0.0 for no location
            if lat and lng and (lat != 0.0 or lng != 0.0):
                latitude = lat
                longitude = lng
                altitude = geo_data.get("altitude")

        # Try geoDataExif as fallback
        if not latitude:
            geo_exif = metadata.get("geoDataExif", {})
            if geo_exif:
                lat = geo_exif.get("latitude")
                lng = geo_exif.get("longitude")
                if lat and lng and (lat != 0.0 or lng != 0.0):
                    latitude = lat
                    longitude = lng
                    altitude = geo_exif.get("altitude")

    # Get album name from parent directory
    if photos_dir:
        try:
            # Album is the directory between photos_dir and the file
            rel_path = media_file.relative_to(photos_dir)
            if len(rel_path.parts) > 1:
                album_name = rel_path.parts[0]
        except ValueError:
            # media_file is not relative to photos_dir
            pass

    return Media(
        entity_type=EntityType.MEDIA,
        source_type=SourceType.GOOGLE_TAKEOUT,
        source_id=str(media_file),
        content_hash=file_hash,
        occurred_at=occurred_at,
        # Media fields
        file_path=str(media_file.resolve()),
        original_filename=title or media_file.name,
        file_size=file_size,
        mime_type=mime_type,
        media_type=media_type,
        file_hash=file_hash,
        # Geo fields from GeolocatedEntity
        latitude=latitude,
        longitude=longitude,
        altitude=altitude,
        # Album
        album_name=album_name,
    )
