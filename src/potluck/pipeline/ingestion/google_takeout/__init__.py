"""Google Takeout ingestion stage.

Handles importing data from Google Takeout archives including:
- Google Photos (photos and videos)
- Google Chat/Hangouts (messages)
- Google Calendar (events)
- Gmail (emails)
- Chrome History/Bookmarks
- Location History (Timeline)
"""

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from potluck.core.logging import get_logger
from potluck.models.base import BaseEntity, EntityType, SourceType
from potluck.pipeline.dtos import DetectionResult, PipelineFilter
from potluck.pipeline.ingestion import BaseIngestionStage, register

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
    ) -> Iterator[BaseEntity]:
        """Yield entities from the Google Takeout archive.

        Routes to per-type ingestion methods based on requested entity types.

        Args:
            path: Path to the extracted takeout directory.
            entity_types: Set of entity types to ingest (None = all supported).
            filters: Optional date range filters.

        Yields:
            Entities of the requested types.
        """
        # Default to all supported types if none specified
        types_to_process = entity_types or self.SUPPORTED_ENTITY_TYPES

        # Only process types that are both requested and supported
        types_to_process = types_to_process & self.SUPPORTED_ENTITY_TYPES

        logger.info(f"Processing Google Takeout at {path} for types: {types_to_process}")

        # Import helper modules lazily to avoid circular imports
        # Each helper module provides a generator function for its entity type

        if EntityType.BROWSING_HISTORY in types_to_process:
            from potluck.pipeline.ingestion.google_takeout.chrome import ingest_browsing_history

            yield from ingest_browsing_history(path, filters)

        if EntityType.BOOKMARK in types_to_process:
            from potluck.pipeline.ingestion.google_takeout.chrome import ingest_bookmarks

            # BookmarkFolder extends SimpleEntity, not BaseEntity, but is valid for ingestion
            yield from ingest_bookmarks(path, filters)  # type: ignore[misc]

        if EntityType.CHAT_MESSAGE in types_to_process:
            from potluck.pipeline.ingestion.google_takeout.chat import ingest_chat_messages

            yield from ingest_chat_messages(path, filters)

        if EntityType.CALENDAR_EVENT in types_to_process:
            from potluck.pipeline.ingestion.google_takeout.calendar import ingest_calendar_events

            yield from ingest_calendar_events(path, filters)

        if EntityType.LOCATION_VISIT in types_to_process:
            from potluck.pipeline.ingestion.google_takeout.location import ingest_location_visits

            yield from ingest_location_visits(path, filters)

        if EntityType.MEDIA in types_to_process:
            from potluck.pipeline.ingestion.google_takeout.photos import ingest_media

            yield from ingest_media(path, filters)

        if EntityType.EMAIL in types_to_process:
            from potluck.pipeline.ingestion.google_takeout.mail import ingest_emails

            yield from ingest_emails(path, filters)

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
            except OSError:
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
        except OSError:
            return 0

    def _count_location_visits(self, path: Path) -> int:
        """Count location visits from Timeline data."""
        # Check for Android Timeline export (Timeline.json)
        timeline_file = path / "Timeline.json"
        if timeline_file.exists():
            # Estimate from file size (each segment ~500 bytes)
            size = timeline_file.stat().st_size
            return max(1, size // 500)

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

    def _find_google_photos_dir(self, path: Path) -> Path | None:
        """Find Google Photos directory in takeout."""
        candidates = [
            path / "Takeout" / "Google Photos",
            path / "Google Photos",
        ]
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        return None

    def _find_google_chat_dir(self, path: Path) -> Path | None:
        """Find Google Chat directory in takeout."""
        candidates = [
            path / "Takeout" / "Google Chat",
            path / "Google Chat",
        ]
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        return None

    def _find_gmail_dir(self, path: Path) -> Path | None:
        """Find Gmail directory in takeout."""
        candidates = [
            path / "Takeout" / "Mail",
            path / "Mail",
        ]
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        return None

    def _find_calendar_dir(self, path: Path) -> Path | None:
        """Find Calendar directory in takeout."""
        candidates = [
            path / "Takeout" / "Calendar",
            path / "Calendar",
        ]
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        return None

    def _find_chrome_dir(self, path: Path) -> Path | None:
        """Find Chrome directory in takeout."""
        candidates = [
            path / "Takeout" / "Chrome",
            path / "Chrome",
        ]
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        return None

    def _find_timeline_dir(self, path: Path) -> Path | None:
        """Find Timeline directory in takeout."""
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
