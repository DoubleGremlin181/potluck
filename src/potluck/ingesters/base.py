"""Base ingester protocol and common types for data ingestion."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from potluck.models.base import EntityType, SourceType

if TYPE_CHECKING:
    from potluck.models import (
        Bookmark,
        BrowsingHistory,
        CalendarEvent,
        ChatMessage,
        Email,
        KnowledgeNote,
        LocationVisit,
        Media,
        Person,
        SocialComment,
        SocialPost,
        Transaction,
    )

# Type alias for ingest methods
IngestMethod = Callable[[Path, "IngestionFilter | None"], Iterator[Any]]


@dataclass
class IngestionFilter:
    """Common filter fields for ingestion operations.

    Allows filtering entities by date range during ingestion.
    Ingesters use these filters to skip entities outside the specified range.
    """

    since: datetime | None = None
    """Only ingest entities occurring on or after this datetime."""

    until: datetime | None = None
    """Only ingest entities occurring before this datetime."""


@dataclass
class EntityCount:
    """Count of entities found during content detection."""

    entity_type: EntityType
    count: int
    description: str | None = None


@dataclass
class DetectionResult:
    """Result of detecting available entity types in a source."""

    entity_counts: dict[EntityType, int] = field(default_factory=dict)
    """Mapping of entity types to their counts."""

    metadata: dict[str, str] = field(default_factory=dict)
    """Additional metadata about the detected content."""

    def total_entities(self) -> int:
        """Get total count of all entities."""
        return sum(self.entity_counts.values())


class BaseIngester(ABC):
    """Abstract base class for data source ingesters.

    Each ingester handles a specific data source (e.g., Google Takeout, Reddit).
    Ingesters are responsible for:
    - Detecting what entity types are available in a given path
    - Parsing and yielding entities from the source data
    - Providing user-facing instructions for obtaining exports

    Subclasses must define class attributes and implement detect_contents().
    They should also implement ingest methods for their supported entity types.
    """

    # Class attributes - must be defined by subclasses
    SOURCE_TYPE: ClassVar[SourceType]
    """The source type enum value for this ingester."""

    DETECTION_PATTERNS: ClassVar[list[str]]
    """Regex patterns that match this source (e.g., r'Takeout-.*\\.zip')."""

    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]]
    """Entity types this ingester can produce."""

    INSTRUCTIONS: ClassVar[str]
    """Markdown instructions for how to obtain this export (for docs/web UI)."""

    @abstractmethod
    def detect_contents(self, path: Path) -> DetectionResult:
        """Scan the source and return available entity types with counts.

        This method should scan the given path (extracted archive or directory)
        and identify what entity types are present and approximately how many
        of each type exist.

        Args:
            path: Path to the extracted source data.

        Returns:
            DetectionResult with entity type counts and metadata.
        """
        ...

    def ingest_media(self, path: Path, filters: IngestionFilter | None = None) -> Iterator["Media"]:
        """Ingest media entities from the source.

        Override this method if the ingester supports EntityType.MEDIA.

        Args:
            path: Path to the source data.
            filters: Optional date range filters.

        Yields:
            Media entities parsed from the source.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support media ingestion")

    def ingest_messages(
        self, path: Path, filters: IngestionFilter | None = None
    ) -> Iterator["ChatMessage"]:
        """Ingest chat message entities from the source.

        Override this method if the ingester supports EntityType.CHAT_MESSAGE.

        Args:
            path: Path to the source data.
            filters: Optional date range filters.

        Yields:
            ChatMessage entities parsed from the source.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support message ingestion")

    def ingest_emails(
        self, path: Path, filters: IngestionFilter | None = None
    ) -> Iterator["Email"]:
        """Ingest email entities from the source.

        Override this method if the ingester supports EntityType.EMAIL.

        Args:
            path: Path to the source data.
            filters: Optional date range filters.

        Yields:
            Email entities parsed from the source.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support email ingestion")

    def ingest_social_posts(
        self, path: Path, filters: IngestionFilter | None = None
    ) -> Iterator["SocialPost"]:
        """Ingest social post entities from the source.

        Override this method if the ingester supports EntityType.SOCIAL_POST.

        Args:
            path: Path to the source data.
            filters: Optional date range filters.

        Yields:
            SocialPost entities parsed from the source.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support social post ingestion"
        )

    def ingest_social_comments(
        self, path: Path, filters: IngestionFilter | None = None
    ) -> Iterator["SocialComment"]:
        """Ingest social comment entities from the source.

        Override this method if the ingester supports EntityType.SOCIAL_COMMENT.

        Args:
            path: Path to the source data.
            filters: Optional date range filters.

        Yields:
            SocialComment entities parsed from the source.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support social comment ingestion"
        )

    def ingest_notes(
        self, path: Path, filters: IngestionFilter | None = None
    ) -> Iterator["KnowledgeNote"]:
        """Ingest knowledge note entities from the source.

        Override this method if the ingester supports EntityType.KNOWLEDGE_NOTE.

        Args:
            path: Path to the source data.
            filters: Optional date range filters.

        Yields:
            KnowledgeNote entities parsed from the source.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support note ingestion")

    def ingest_calendar_events(
        self, path: Path, filters: IngestionFilter | None = None
    ) -> Iterator["CalendarEvent"]:
        """Ingest calendar event entities from the source.

        Override this method if the ingester supports EntityType.CALENDAR_EVENT.

        Args:
            path: Path to the source data.
            filters: Optional date range filters.

        Yields:
            CalendarEvent entities parsed from the source.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support calendar event ingestion"
        )

    def ingest_transactions(
        self, path: Path, filters: IngestionFilter | None = None
    ) -> Iterator["Transaction"]:
        """Ingest transaction entities from the source.

        Override this method if the ingester supports EntityType.TRANSACTION.

        Args:
            path: Path to the source data.
            filters: Optional date range filters.

        Yields:
            Transaction entities parsed from the source.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support transaction ingestion"
        )

    def ingest_location_visits(
        self, path: Path, filters: IngestionFilter | None = None
    ) -> Iterator["LocationVisit"]:
        """Ingest location visit entities from the source.

        Override this method if the ingester supports EntityType.LOCATION_VISIT.

        Args:
            path: Path to the source data.
            filters: Optional date range filters.

        Yields:
            LocationVisit entities parsed from the source.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support location visit ingestion"
        )

    def ingest_browsing_history(
        self, path: Path, filters: IngestionFilter | None = None
    ) -> Iterator["BrowsingHistory"]:
        """Ingest browsing history entities from the source.

        Override this method if the ingester supports EntityType.BROWSING_HISTORY.

        Args:
            path: Path to the source data.
            filters: Optional date range filters.

        Yields:
            BrowsingHistory entities parsed from the source.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support browsing history ingestion"
        )

    def ingest_bookmarks(
        self, path: Path, filters: IngestionFilter | None = None
    ) -> Iterator["Bookmark"]:
        """Ingest bookmark entities from the source.

        Override this method if the ingester supports EntityType.BOOKMARK.

        Args:
            path: Path to the source data.
            filters: Optional date range filters.

        Yields:
            Bookmark entities parsed from the source.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support bookmark ingestion")

    def ingest_people(
        self, path: Path, filters: IngestionFilter | None = None
    ) -> Iterator["Person"]:
        """Ingest person entities from the source.

        Override this method if the ingester supports EntityType.PERSON.

        Args:
            path: Path to the source data.
            filters: Optional date range filters.

        Yields:
            Person entities parsed from the source.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support person ingestion")

    def get_ingest_method(self, entity_type: EntityType) -> "IngestMethod":
        """Get the ingest method for a given entity type.

        Args:
            entity_type: The entity type to get the method for.

        Returns:
            The ingest method callable.

        Raises:
            ValueError: If the entity type is not supported.
        """
        method_map: dict[EntityType, IngestMethod] = {
            EntityType.MEDIA: self.ingest_media,
            EntityType.CHAT_MESSAGE: self.ingest_messages,
            EntityType.EMAIL: self.ingest_emails,
            EntityType.SOCIAL_POST: self.ingest_social_posts,
            EntityType.SOCIAL_COMMENT: self.ingest_social_comments,
            EntityType.KNOWLEDGE_NOTE: self.ingest_notes,
            EntityType.CALENDAR_EVENT: self.ingest_calendar_events,
            EntityType.TRANSACTION: self.ingest_transactions,
            EntityType.LOCATION_VISIT: self.ingest_location_visits,
            EntityType.BROWSING_HISTORY: self.ingest_browsing_history,
            EntityType.BOOKMARK: self.ingest_bookmarks,
            EntityType.PERSON: self.ingest_people,
        }
        if entity_type not in method_map:
            raise ValueError(f"Unknown entity type: {entity_type}")
        return method_map[entity_type]
