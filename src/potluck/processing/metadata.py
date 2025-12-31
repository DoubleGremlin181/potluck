"""EXIF metadata extraction processor for media files.

This module extracts metadata from images including:
- GPS coordinates (converted from DMS to decimal degrees)
- Timestamps (with handling for various date formats)
- Camera make and model
- Full EXIF data stored as JSON

Issue #26: EXIF metadata extraction
"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import exifread

from potluck.core.exceptions import ProcessingError
from potluck.core.logging import get_logger
from potluck.models.media import Media, MediaType
from potluck.processing.base import BaseProcessor, ProcessingResult, ProcessingStatus

logger = get_logger(__name__)


# Common EXIF date formats from various camera manufacturers
EXIF_DATE_FORMATS = [
    "%Y:%m:%d %H:%M:%S",  # Standard EXIF format
    "%Y-%m-%d %H:%M:%S",  # ISO-ish format
    "%Y/%m/%d %H:%M:%S",  # Alternative format
    "%Y:%m:%d %H:%M:%S.%f",  # With microseconds
    "%Y-%m-%dT%H:%M:%S",  # ISO 8601
    "%Y-%m-%dT%H:%M:%SZ",  # ISO 8601 UTC
]


class MetadataProcessor(BaseProcessor):
    """Processor for extracting EXIF metadata from images.

    Extracts:
    - GPS coordinates (latitude, longitude converted to decimal)
    - Timestamp (occurred_at from DateTimeOriginal or DateTime)
    - Camera make and model
    - Full EXIF data as JSON for reference
    """

    NAME = "metadata"

    def should_process(self, media: Media) -> bool:
        """Only process images which typically have EXIF data."""
        return media.media_type == MediaType.IMAGE

    def process(self, media: Media) -> ProcessingResult:
        """Extract EXIF metadata from an image file.

        Args:
            media: Media item to process.

        Returns:
            ProcessingResult with extracted metadata.
        """
        start_time = time.monotonic()

        if not self.should_process(media):
            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.SKIPPED,
                error_message="Not an image file",
            )

        try:
            path = Path(media.file_path)

            if not path.exists():
                return ProcessingResult(
                    media_id=media.id,
                    processor_name=self.NAME,
                    status=ProcessingStatus.FAILED,
                    error_message=f"File not found: {media.file_path}",
                )

            # Read EXIF data
            with open(path, "rb") as f:
                tags = exifread.process_file(f, details=False)

            if not tags:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                return ProcessingResult(
                    media_id=media.id,
                    processor_name=self.NAME,
                    status=ProcessingStatus.COMPLETED,
                    processing_time_ms=elapsed_ms,
                    data={"exif_data": None, "has_exif": False},
                )

            # Extract specific fields
            latitude, longitude = self._extract_gps(tags)
            occurred_at = self._extract_datetime(tags)
            camera_make = self._get_tag_value(tags, "Image Make")
            camera_model = self._get_tag_value(tags, "Image Model")

            # Serialize all EXIF data to JSON
            exif_data = self._serialize_exif(tags)

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.COMPLETED,
                processing_time_ms=elapsed_ms,
                data={
                    "latitude": latitude,
                    "longitude": longitude,
                    "occurred_at": occurred_at.isoformat() if occurred_at else None,
                    "camera_make": camera_make,
                    "camera_model": camera_model,
                    "exif_data": exif_data,
                    "has_exif": True,
                },
            )

        except ProcessingError as e:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.FAILED,
                error_message=str(e),
                processing_time_ms=elapsed_ms,
            )
        except Exception as e:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            logger.exception(f"Metadata extraction failed for {media.file_path}: {e}")
            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.FAILED,
                error_message=f"Metadata extraction failed: {e}",
                processing_time_ms=elapsed_ms,
            )

    def _extract_gps(self, tags: dict[str, Any]) -> tuple[float | None, float | None]:
        """Extract GPS coordinates from EXIF tags.

        Converts from degrees/minutes/seconds (DMS) to decimal degrees.

        Args:
            tags: EXIF tags dictionary.

        Returns:
            Tuple of (latitude, longitude) in decimal degrees, or (None, None).
        """
        try:
            lat_tag = tags.get("GPS GPSLatitude")
            lat_ref = tags.get("GPS GPSLatitudeRef")
            lon_tag = tags.get("GPS GPSLongitude")
            lon_ref = tags.get("GPS GPSLongitudeRef")

            if not all([lat_tag, lat_ref, lon_tag, lon_ref]):
                return None, None

            # Access the values attribute from the EXIF tag objects
            latitude = self._dms_to_decimal(lat_tag.values)  # type: ignore[union-attr]
            longitude = self._dms_to_decimal(lon_tag.values)  # type: ignore[union-attr]

            # Apply reference direction
            if str(lat_ref) == "S":
                latitude = -latitude
            if str(lon_ref) == "W":
                longitude = -longitude

            return latitude, longitude

        except Exception as e:
            logger.debug(f"Failed to extract GPS: {e}")
            return None, None

    def _dms_to_decimal(self, dms_values: list[Any]) -> float:
        """Convert degrees/minutes/seconds to decimal degrees.

        Args:
            dms_values: List of [degrees, minutes, seconds] as Ratio objects.

        Returns:
            Decimal degrees value.
        """
        degrees = float(dms_values[0])
        minutes = float(dms_values[1])
        seconds = float(dms_values[2])

        return degrees + (minutes / 60.0) + (seconds / 3600.0)

    def _extract_datetime(self, tags: dict[str, Any]) -> datetime | None:
        """Extract timestamp from EXIF tags.

        Tries multiple date fields in order of preference:
        1. EXIF DateTimeOriginal (when photo was taken)
        2. EXIF DateTimeDigitized (when photo was digitized)
        3. Image DateTime (file modification time)

        Args:
            tags: EXIF tags dictionary.

        Returns:
            datetime object or None if no valid date found.
        """
        date_tags = [
            "EXIF DateTimeOriginal",
            "EXIF DateTimeDigitized",
            "Image DateTime",
        ]

        for tag_name in date_tags:
            tag = tags.get(tag_name)
            if tag:
                dt = self._parse_exif_datetime(str(tag))
                if dt:
                    return dt

        return None

    def _parse_exif_datetime(self, date_str: str) -> datetime | None:
        """Parse EXIF datetime string with multiple format support.

        Args:
            date_str: Date string from EXIF tag.

        Returns:
            datetime object or None if parsing fails.
        """
        for fmt in EXIF_DATE_FORMATS:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                # Assume UTC if no timezone info
                return dt.replace(tzinfo=UTC)
            except ValueError:
                continue

        logger.debug(f"Could not parse EXIF date: {date_str}")
        return None

    def _get_tag_value(self, tags: dict[str, Any], tag_name: str) -> str | None:
        """Get string value from an EXIF tag.

        Args:
            tags: EXIF tags dictionary.
            tag_name: Name of the tag to retrieve.

        Returns:
            String value or None if tag not present.
        """
        tag = tags.get(tag_name)
        if tag:
            value = str(tag).strip()
            return value if value else None
        return None

    def _serialize_exif(self, tags: dict[str, Any]) -> str:
        """Serialize EXIF tags to JSON string.

        Converts all tags to string representation for storage.

        Args:
            tags: EXIF tags dictionary.

        Returns:
            JSON string of EXIF data.
        """
        exif_dict = {}
        for key, value in tags.items():
            # Skip thumbnail data (can be large)
            if key.startswith("Thumbnail") or key.startswith("EXIF MakerNote"):
                continue
            try:
                exif_dict[key] = str(value)
            except Exception:
                exif_dict[key] = "<unserializable>"

        return json.dumps(exif_dict, ensure_ascii=False)
