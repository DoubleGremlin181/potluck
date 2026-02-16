"""Media file ingestion from arbitrary folders."""

import mimetypes
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import exifread

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.utils.hashing import compute_file_hash
from potluck.pipeline.utils.media import (
    ALL_MEDIA_EXTENSIONS,
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)

logger = get_logger(__name__)


def ingest_media(
    path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[Media]:
    """Ingest media files from a directory.

    Recursively scans for image, video, and audio files.

    Args:
        path: Directory to scan.
        filters: Optional date range filters.

    Yields:
        Media entities.
    """
    if path.is_file():
        entity = _process_file(path, path.parent, filters)
        if entity:
            yield entity
        return

    logger.info(f"Processing media folder at {path}")

    for file_path in path.rglob("*"):
        if not file_path.is_file():
            continue

        if file_path.suffix.lower() not in ALL_MEDIA_EXTENSIONS:
            continue

        entity = _process_file(file_path, path, filters)
        if entity:
            yield entity


def count_media_files(path: Path) -> int:
    """Count media files by extension in a directory."""
    if path.is_file():
        return 1 if path.suffix.lower() in ALL_MEDIA_EXTENSIONS else 0

    count = 0
    for file_path in path.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in ALL_MEDIA_EXTENSIONS:
            count += 1
    return count


def _process_file(
    file_path: Path,
    base_path: Path,
    filters: PipelineFilter | None,
) -> Media | None:
    """Process a single media file into a Media entity."""
    suffix = file_path.suffix.lower()
    media_type = _get_media_type(suffix)

    # Extract EXIF date for occurred_at (images only)
    occurred_at = None
    if media_type == MediaType.IMAGE:
        occurred_at = _extract_exif_date(file_path)

    # Fall back to file mtime
    if occurred_at is None:
        try:
            mtime = file_path.stat().st_mtime
            occurred_at = datetime.fromtimestamp(mtime, tz=UTC)
        except OSError:
            pass

    # Apply date filters
    if filters and occurred_at:
        if filters.since and occurred_at < filters.since:
            return None
        if filters.until and occurred_at >= filters.until:
            return None

    # Compute file hash
    try:
        file_hash = compute_file_hash(file_path)
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to compute hash for {file_path}: {e}")
        file_hash = None

    # Get file size and MIME type
    try:
        file_size = file_path.stat().st_size
    except OSError:
        file_size = None

    mime_type, _ = mimetypes.guess_type(str(file_path))

    # Album name from relative directory path
    album_name = None
    try:
        rel_path = file_path.relative_to(base_path)
        if len(rel_path.parts) > 1:
            album_name = str(Path(*rel_path.parts[:-1]))
    except ValueError:
        pass

    return Media(
        source_type=SourceType.IMAGE_FOLDER,
        source_id=str(file_path),
        content_hash=file_hash,
        occurred_at=occurred_at,
        file_path=str(file_path.resolve()),
        original_filename=file_path.name,
        file_size=file_size,
        mime_type=mime_type,
        media_type=media_type,
        file_hash=file_hash,
        album_name=album_name,
    )


def _get_media_type(suffix: str) -> MediaType:
    """Determine MediaType from file extension."""
    if suffix in IMAGE_EXTENSIONS:
        return MediaType.IMAGE
    if suffix in VIDEO_EXTENSIONS:
        return MediaType.VIDEO
    if suffix in AUDIO_EXTENSIONS:
        return MediaType.AUDIO
    return MediaType.OTHER


def _extract_exif_date(file_path: Path) -> datetime | None:
    """Extract DateTimeOriginal from EXIF data."""
    try:
        with file_path.open("rb") as f:
            tags = exifread.process_file(f, stop_tag="EXIF DateTimeOriginal", details=False)

        date_tag = tags.get("EXIF DateTimeOriginal")
        if not date_tag:
            return None

        # EXIF date format: "2024:01:15 10:30:00"
        return datetime.strptime(str(date_tag), "%Y:%m:%d %H:%M:%S").replace(tzinfo=UTC)
    except (OSError, ValueError, KeyError):
        return None
