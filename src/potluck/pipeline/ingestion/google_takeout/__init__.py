"""Google Takeout ingestion stage.

Handles importing data from Google Takeout archives including:
- Google Photos (photos and videos)
- Google Chat/Hangouts (messages)
- Google Calendar (events)
- Gmail (emails)
- Chrome History/Bookmarks
- Location History (Timeline)

Supports both extracted directories and compressed archives (.zip, .tgz, .tar.gz).
"""

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from potluck.core.logging import get_logger
from potluck.models.base import EntityType, IngestableEntity, SourceType
from potluck.pipeline.dtos import DetectionResult, PipelineFilter
from potluck.pipeline.ingestion.base import BaseIngestionStage
from potluck.pipeline.ingestion.google_takeout.calendar import ingest_calendar_events
from potluck.pipeline.ingestion.google_takeout.chat import ingest_chat_messages
from potluck.pipeline.ingestion.google_takeout.chrome import (
    ingest_bookmarks,
    ingest_browsing_history,
)
from potluck.pipeline.ingestion.google_takeout.location import ingest_location_visits
from potluck.pipeline.ingestion.google_takeout.mail import ingest_emails
from potluck.pipeline.ingestion.google_takeout.photos import ingest_media
from potluck.pipeline.ingestion.registry import register
from potluck.pipeline.utils.archive import extracted

logger = get_logger(__name__)


@register
class GoogleTakeoutStage(BaseIngestionStage):
    """Ingestion stage for Google Takeout archives.

    Handles multiple entity types from a single Google Takeout export:
    - Media: Photos and videos from Google Photos
    - Chat Messages: Messages from Google Chat/Hangouts
    - Emails: Messages from Gmail
    - Calendar Events: Events from Google Calendar
    - Browsing History: Chrome browsing history
    - Bookmarks: Chrome bookmarks
    - Location Visits: Location history from Timeline

    The stage auto-detects which data types are present in the archive
    and only processes the requested entity types.
    """

    SOURCE_TYPE: ClassVar[SourceType] = SourceType.GOOGLE_TAKEOUT

    FILENAME_PATTERNS: ClassVar[list[str]] = [
        r"takeout-.*\.(zip|tgz|tar\.gz)",  # Standard Google Takeout pattern
        r"Takeout",  # Extracted takeout folder
    ]

    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {
        EntityType.MEDIA,
        EntityType.CHAT_MESSAGE,
        EntityType.EMAIL,
        EntityType.CALENDAR_EVENT,
        EntityType.BROWSING_HISTORY,
        EntityType.BOOKMARK,
        EntityType.LOCATION_VISIT,
    }

    def detect(self, path: Path) -> DetectionResult:
        """Scan the takeout and return available entity types with counts.

        Supports both extracted directories and compressed archives.
        For archives, extracts to a temporary directory for detection.

        Args:
            path: Path to the extracted takeout directory or archive file.

        Returns:
            DetectionResult with entity type counts and metadata.
        """
        # Handle archives by extracting to temp directory for detection
        with extracted(path) as content_path:
            return self._detect_from_path(content_path)

    def _detect_from_path(self, path: Path) -> DetectionResult:
        """Perform detection on an extracted path.

        Args:
            path: Path to the extracted takeout directory.

        Returns:
            DetectionResult with entity type counts and metadata.
        """
        entity_counts: dict[EntityType, int] = {}
        metadata: dict[str, str] = {}

        # Detect Google Photos
        photos_count = self._count_photos(path)
        if photos_count > 0:
            entity_counts[EntityType.MEDIA] = photos_count

        # Detect Google Chat
        chat_count = self._count_chat_messages(path)
        if chat_count > 0:
            entity_counts[EntityType.CHAT_MESSAGE] = chat_count

        # Detect Gmail
        email_count = self._count_emails(path)
        if email_count > 0:
            entity_counts[EntityType.EMAIL] = email_count

        # Detect Calendar
        calendar_count = self._count_calendar_events(path)
        if calendar_count > 0:
            entity_counts[EntityType.CALENDAR_EVENT] = calendar_count

        # Detect Chrome History
        history_count = self._count_browsing_history(path)
        if history_count > 0:
            entity_counts[EntityType.BROWSING_HISTORY] = history_count

        # Detect Chrome Bookmarks
        bookmark_count = self._count_bookmarks(path)
        if bookmark_count > 0:
            entity_counts[EntityType.BOOKMARK] = bookmark_count

        # Detect Location History
        location_count = self._count_location_visits(path)
        if location_count > 0:
            entity_counts[EntityType.LOCATION_VISIT] = location_count

        # Add metadata about detected sources
        if entity_counts:
            metadata["source"] = "Google Takeout"
            detected_types = [et.value for et in entity_counts]
            metadata["detected_types"] = ", ".join(sorted(detected_types))

        return DetectionResult(entity_counts=entity_counts, metadata=metadata)

    def execute(
        self,
        path: Path,
        entity_types: set[EntityType] | None = None,
        filters: PipelineFilter | None = None,
    ) -> Iterator[IngestableEntity]:
        """Yield entities from the Google Takeout archive.

        Supports both extracted directories and compressed archives.
        For archives, extracts to a temporary directory during processing.

        Routes to per-type ingestion methods based on requested entity types.

        Args:
            path: Path to the extracted takeout directory or archive file.
            entity_types: Set of entity types to ingest (None = all supported).
            filters: Optional date range filters.

        Yields:
            Entities of the requested types.
        """
        # Handle archives by extracting to temp directory
        with extracted(path) as content_path:
            yield from self._execute_from_path(content_path, entity_types, filters)

    def _execute_from_path(
        self,
        path: Path,
        entity_types: set[EntityType] | None = None,
        filters: PipelineFilter | None = None,
    ) -> Iterator[IngestableEntity]:
        """Execute ingestion on an extracted path.

        Args:
            path: Path to the extracted takeout directory.
            entity_types: Set of entity types to ingest (None = all supported).
            filters: Optional date range filters.

        Yields:
            Entities of the requested types, deduplicated by content_hash.
        """
        # Default to all supported types if none specified
        types_to_process = entity_types or self.SUPPORTED_ENTITY_TYPES

        # Only process types that are both requested and supported
        types_to_process = types_to_process & self.SUPPORTED_ENTITY_TYPES

        logger.info(f"Processing Google Takeout at {path} for types: {types_to_process}")

        # Track seen content hashes for in-memory deduplication
        # Note: DB ON CONFLICT provides a safety net for duplicates that
        # slip through (e.g., from separate ingestion runs)
        seen_hashes: set[str] = set()

        def deduplicate(
            entities: Iterator[IngestableEntity],
        ) -> Iterator[IngestableEntity]:
            """Skip entities with duplicate content_hash."""
            for entity in entities:
                content_hash = getattr(entity, "content_hash", None)
                if content_hash:
                    if content_hash in seen_hashes:
                        continue
                    seen_hashes.add(content_hash)
                yield entity

        if EntityType.BROWSING_HISTORY in types_to_process:
            yield from deduplicate(ingest_browsing_history(path, filters))

        if EntityType.BOOKMARK in types_to_process:
            yield from deduplicate(ingest_bookmarks(path, filters))

        if EntityType.CHAT_MESSAGE in types_to_process:
            yield from deduplicate(ingest_chat_messages(path, filters))

        if EntityType.CALENDAR_EVENT in types_to_process:
            yield from deduplicate(ingest_calendar_events(path, filters))

        if EntityType.LOCATION_VISIT in types_to_process:
            yield from deduplicate(ingest_location_visits(path, filters))

        if EntityType.MEDIA in types_to_process:
            yield from deduplicate(ingest_media(path, filters))

        if EntityType.EMAIL in types_to_process:
            yield from deduplicate(ingest_emails(path, filters))

    # -------------------------------------------------------------------------
    # Detection helper methods
    # -------------------------------------------------------------------------

    def _count_photos(self, path: Path) -> int:
        """Count media files in Google Photos directory."""
        photos_dir = self._find_google_photos_dir(path)
        if not photos_dir:
            return 0

        # Count image and video files
        media_extensions = {
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".heic",
            ".heif",
            ".mp4",
            ".mov",
            ".avi",
            ".mkv",
            ".webm",
        }
        count = 0
        for file in photos_dir.rglob("*"):
            if file.is_file() and file.suffix.lower() in media_extensions:
                count += 1
        return count

    def _count_chat_messages(self, path: Path) -> int:
        """Count chat message files in Google Chat directory."""
        chat_dir = self._find_google_chat_dir(path)
        if not chat_dir:
            return 0

        # Count messages.json files (each contains multiple messages)
        count = 0
        for messages_file in chat_dir.rglob("messages.json"):
            # Estimate count from file size (rough approximation)
            # Each message is roughly 500 bytes on average
            size = messages_file.stat().st_size
            count += max(1, size // 500)
        return count

    def _count_emails(self, path: Path) -> int:
        """Count emails in Gmail mbox files."""
        mail_dir = self._find_gmail_dir(path)
        if not mail_dir:
            return 0

        # Count messages in mbox files (rough estimate from file size)
        count = 0
        for mbox_file in mail_dir.rglob("*.mbox"):
            # Each email is roughly 10KB on average
            size = mbox_file.stat().st_size
            count += max(1, size // 10000)
        return count

    def _count_calendar_events(self, path: Path) -> int:
        """Count calendar events in ICS files."""
        calendar_dir = self._find_calendar_dir(path)
        if not calendar_dir:
            return 0

        # Count VEVENT occurrences in ICS files
        count = 0
        for ics_file in calendar_dir.rglob("*.ics"):
            try:
                content = ics_file.read_text(encoding="utf-8", errors="replace")
                count += content.count("BEGIN:VEVENT")
            except OSError as e:
                logger.warning(f"Failed to read calendar file {ics_file}: {e}")
                continue
        return count

    def _count_browsing_history(self, path: Path) -> int:
        """Count browsing history entries."""
        chrome_dir = self._find_chrome_dir(path)
        if not chrome_dir:
            return 0

        history_file = chrome_dir / "BrowserHistory.json"
        if not history_file.exists():
            return 0

        # Estimate from file size (each entry ~200 bytes)
        size = history_file.stat().st_size
        return max(1, size // 200)

    def _count_bookmarks(self, path: Path) -> int:
        """Count bookmarks in Chrome bookmarks file."""
        chrome_dir = self._find_chrome_dir(path)
        if not chrome_dir:
            return 0

        bookmarks_file = chrome_dir / "Bookmarks.html"
        if not bookmarks_file.exists():
            return 0

        # Count <A HREF= occurrences in HTML
        try:
            content = bookmarks_file.read_text(encoding="utf-8", errors="replace")
            return content.lower().count("<a href=")
        except OSError as e:
            logger.warning(f"Failed to read bookmarks file {bookmarks_file}: {e}")
            return 0

    def _count_location_visits(self, path: Path) -> int:
        """Count location visits from Google Takeout Timeline data.

        Note: Android Timeline export (Timeline.json at root) is handled
        by the separate AndroidTimelineStage.
        """
        # Check for Google Takeout Timeline
        timeline_dir = self._find_timeline_dir(path)
        if not timeline_dir:
            return 0

        # Check Timeline Edits.json
        edits_file = timeline_dir / "Timeline Edits.json"
        if edits_file.exists():
            size = edits_file.stat().st_size
            return max(1, size // 200)

        return 0

    # -------------------------------------------------------------------------
    # Directory finding helpers
    # -------------------------------------------------------------------------

    def _find_takeout_dir(self, path: Path, name: str, *alternate_names: str) -> Path | None:
        """Find a directory within Takeout structure.

        Checks both under Takeout/ prefix and at root level.
        Supports alternate names (e.g., "Timeline" vs "Location History").

        Args:
            path: Base path to search from.
            name: Primary directory name to find.
            *alternate_names: Optional alternate names to check.

        Returns:
            Path to the directory if found, None otherwise.
        """
        all_names = [name, *alternate_names]
        for dir_name in all_names:
            candidates = [path / "Takeout" / dir_name, path / dir_name]
            for candidate in candidates:
                if candidate.is_dir():
                    return candidate
        return None

    def _find_google_photos_dir(self, path: Path) -> Path | None:
        """Find Google Photos directory in takeout."""
        return self._find_takeout_dir(path, "Google Photos")

    def _find_google_chat_dir(self, path: Path) -> Path | None:
        """Find Google Chat directory in takeout."""
        return self._find_takeout_dir(path, "Google Chat")

    def _find_gmail_dir(self, path: Path) -> Path | None:
        """Find Gmail directory in takeout."""
        return self._find_takeout_dir(path, "Mail")

    def _find_calendar_dir(self, path: Path) -> Path | None:
        """Find Calendar directory in takeout."""
        return self._find_takeout_dir(path, "Calendar")

    def _find_chrome_dir(self, path: Path) -> Path | None:
        """Find Chrome directory in takeout."""
        return self._find_takeout_dir(path, "Chrome")

    def _find_timeline_dir(self, path: Path) -> Path | None:
        """Find Timeline directory in takeout."""
        return self._find_takeout_dir(path, "Timeline", "Location History")
