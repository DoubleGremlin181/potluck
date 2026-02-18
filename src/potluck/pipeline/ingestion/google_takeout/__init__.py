"""Google Takeout ingestion stage.

Handles importing data from Google Takeout archives including:
- Google Photos (photos and videos)
- Google Chat/Hangouts (messages)
- Google Calendar (events)
- Gmail (emails)
- Chrome History/Bookmarks
- Location History (Timeline)
- Google Keep (notes)

Supports both extracted directories and compressed archives (.zip, .tgz, .tar.gz).
"""

import json
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
from potluck.pipeline.ingestion.google_takeout.keep import count_keep_notes, ingest_keep_notes
from potluck.pipeline.ingestion.google_takeout.location import ingest_location_visits
from potluck.pipeline.ingestion.google_takeout.mail import ingest_emails
from potluck.pipeline.ingestion.google_takeout.photos import ingest_media
from potluck.pipeline.ingestion.registry import register
from potluck.pipeline.utils.archive import extracted
from potluck.pipeline.utils.media import PHOTO_VIDEO_EXTENSIONS

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
    - Documents: Notes from Google Keep

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
        EntityType.DOCUMENT,
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

        # Detect Google Keep
        keep_count = count_keep_notes(path)
        if keep_count > 0:
            entity_counts[EntityType.DOCUMENT] = keep_count

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
            Dependent entities (e.g., EventParticipant) are also skipped if
            their parent entity was deduplicated to prevent FK orphans.
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
        # Track IDs of skipped entities so we can skip their dependents
        skipped_entity_ids: set[str] = set()

        def deduplicate(
            entities: Iterator[IngestableEntity],
        ) -> Iterator[IngestableEntity]:
            """Skip entities with duplicate content_hash and their dependents."""
            for entity in entities:
                # Check if this entity references a skipped parent
                # (e.g., EventParticipant referencing a skipped CalendarEvent)
                fk_fields = ["event_id", "email_id", "thread_id", "folder_id", "parent_id"]
                references_skipped = False
                for field in fk_fields:
                    fk_value = getattr(entity, field, None)
                    if fk_value is not None and str(fk_value) in skipped_entity_ids:
                        references_skipped = True
                        break

                if references_skipped:
                    # Skip dependent entity whose parent was skipped
                    continue

                content_hash = getattr(entity, "content_hash", None)
                if content_hash:
                    if content_hash in seen_hashes:
                        # Track skipped entity ID so dependents are also skipped
                        entity_id = getattr(entity, "id", None)
                        if entity_id:
                            skipped_entity_ids.add(str(entity_id))
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

        if EntityType.DOCUMENT in types_to_process:
            yield from deduplicate(ingest_keep_notes(path, filters))

    # -------------------------------------------------------------------------
    # Detection helper methods
    # -------------------------------------------------------------------------

    def _count_photos(self, path: Path) -> int:
        """Count media files in Google Photos directory."""
        photos_dir = self._find_google_photos_dir(path)
        if not photos_dir:
            return 0

        count = 0
        for file in photos_dir.rglob("*"):
            if file.is_file() and file.suffix.lower() in PHOTO_VIDEO_EXTENSIONS:
                count += 1
        return count

    def _count_chat_messages(self, path: Path) -> int:
        """Count chat messages by parsing messages.json files."""
        chat_dir = self._find_google_chat_dir(path)
        if not chat_dir:
            return 0

        count = 0
        for messages_file in chat_dir.rglob("messages.json"):
            try:
                data = json.loads(messages_file.read_text(encoding="utf-8"))
                count += len(data.get("messages", []))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(f"Failed to parse {messages_file}: {e}")
        return count

    def _count_emails(self, path: Path) -> int:
        """Count emails in Gmail mbox files by counting 'From ' separators."""
        mail_dir = self._find_gmail_dir(path)
        if not mail_dir:
            return 0

        count = 0
        for mbox_file in mail_dir.rglob("*.mbox"):
            try:
                with mbox_file.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if line.startswith("From "):
                            count += 1
            except OSError as e:
                logger.warning(f"Failed to read {mbox_file}: {e}")
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
        """Count browsing history entries by parsing BrowserHistory.json."""
        chrome_dir = self._find_chrome_dir(path)
        if not chrome_dir:
            return 0

        history_file = chrome_dir / "BrowserHistory.json"
        if not history_file.exists():
            return 0

        try:
            data = json.loads(history_file.read_text(encoding="utf-8"))
            return len(data.get("Browser History", []))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Failed to parse {history_file}: {e}")
            return 0

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
        """Count location visits by parsing Timeline Edits.json.

        Note: Android Timeline export (Timeline.json at root) is handled
        by the separate AndroidTimelineStage.
        """
        timeline_dir = self._find_timeline_dir(path)
        if not timeline_dir:
            return 0

        edits_file = timeline_dir / "Timeline Edits.json"
        if not edits_file.exists():
            return 0

        try:
            data = json.loads(edits_file.read_text(encoding="utf-8"))
            return len(data.get("timelineEdits", []))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Failed to parse {edits_file}: {e}")
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
