"""Base ingester protocol and common types for data ingestion."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from potluck.models.base import EntityType, SourceType

# Type alias for ingest methods
IngestMethod = Callable[[Path, "IngestionFilter | None"], Iterator[Any]]


# Module-level mapping of entity types to ingest method names
ENTITY_TYPE_METHOD_MAP: dict[EntityType, str] = {
    EntityType.MEDIA: "ingest_media",
    EntityType.CHAT_MESSAGE: "ingest_messages",
    EntityType.EMAIL: "ingest_emails",
    EntityType.SOCIAL_POST: "ingest_social_posts",
    EntityType.SOCIAL_COMMENT: "ingest_social_comments",
    EntityType.KNOWLEDGE_NOTE: "ingest_notes",
    EntityType.CALENDAR_EVENT: "ingest_calendar_events",
    EntityType.TRANSACTION: "ingest_transactions",
    EntityType.LOCATION_VISIT: "ingest_location_visits",
    EntityType.BROWSING_HISTORY: "ingest_browsing_history",
    EntityType.BOOKMARK: "ingest_bookmarks",
    EntityType.PERSON: "ingest_people",
}


class IngestionFilter(BaseModel):
    """Common filter fields for ingestion operations.

    Allows filtering entities by date range during ingestion.
    Ingesters use these filters to skip entities outside the specified range.
    """

    since: datetime | None = Field(
        default=None,
        description="Only ingest entities occurring on or after this datetime",
    )
    until: datetime | None = Field(
        default=None,
        description="Only ingest entities occurring before this datetime",
    )


class DetectionResult(BaseModel):
    """Result of detecting available entity types in a source."""

    entity_counts: dict[EntityType, int] = Field(default_factory=dict)
    """Mapping of entity types to their counts."""

    metadata: dict[str, str] = Field(default_factory=dict)
    """Additional metadata about the detected content."""

    def total_entities(self) -> int:
        """Get total count of all entities."""
        return sum(self.entity_counts.values())

    model_config = {"arbitrary_types_allowed": True}


class BaseIngester(ABC):
    """Abstract base class for data source ingesters.

    Each ingester handles a specific data source (e.g., Google Takeout, Reddit).
    Ingesters are responsible for:
    - Detecting what entity types are available in a given path
    - Parsing and yielding entities from the source data
    - Providing user-facing instructions for obtaining exports

    Subclasses must define class attributes and implement detect_contents().
    They should also implement ingest methods for their supported entity types
    (e.g., ingest_media, ingest_messages, etc.).
    """

    # Class attributes - must be defined by subclasses
    SOURCE_TYPE: ClassVar[SourceType]
    """The source type enum value for this ingester."""

    FILENAME_PATTERNS: ClassVar[list[str]]
    """Regex patterns matching source file/directory names (e.g., r'Takeout-.*\\.zip')."""

    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]]
    """Entity types this ingester can produce."""

    SUPPORTED_EXTENSIONS: ClassVar[dict[str, EntityType]] = {}
    """File extensions this ingester handles (for generic detection)."""

    INSTRUCTIONS: ClassVar[str] = ""
    """Markdown instructions for how to obtain this export (fallback if no file)."""

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

    @classmethod
    def get_instructions(cls) -> str:
        """Load instructions from resource file, fallback to class attribute.

        Instructions are loaded from:
        potluck/ingesters/resources/instructions/{source_type.value}.md

        Returns:
            Markdown instructions for obtaining this data export.
        """
        try:
            from importlib.resources import files

            resource = files("potluck.ingesters.resources.instructions").joinpath(
                f"{cls.SOURCE_TYPE.value}.md"
            )
            return resource.read_text()
        except (FileNotFoundError, AttributeError, TypeError):
            return cls.INSTRUCTIONS

    @classmethod
    def get_instructions_media_path(cls) -> Path | None:
        """Get path to media folder for this ingester's instructions.

        Media files (images, etc.) for instructions are stored in:
        potluck/ingesters/resources/instructions/media/{source_type.value}/

        Returns:
            Path to the media folder, or None if it doesn't exist.
        """
        try:
            from importlib.resources import files

            media_resource = files("potluck.ingesters.resources.instructions.media").joinpath(
                cls.SOURCE_TYPE.value
            )
            media_path = Path(str(media_resource))
            return media_path if media_path.is_dir() else None
        except (FileNotFoundError, AttributeError, TypeError):
            return None

    def get_ingest_method(self, entity_type: EntityType) -> IngestMethod:
        """Get the ingest method for a given entity type.

        Subclasses should implement the specific ingest methods they support
        (e.g., ingest_media, ingest_messages, etc.).

        Args:
            entity_type: The entity type to get the method for.

        Returns:
            The ingest method callable.

        Raises:
            ValueError: If the entity type is unknown.
            NotImplementedError: If the ingester doesn't support this entity type.
        """
        method_name = ENTITY_TYPE_METHOD_MAP.get(entity_type)
        if method_name is None:
            raise ValueError(f"Unknown entity type: {entity_type}")

        method: IngestMethod | None = getattr(self, method_name, None)
        if method is None:
            raise NotImplementedError(
                f"{self.__class__.__name__} does not support {entity_type.value} ingestion"
            )
        return method
