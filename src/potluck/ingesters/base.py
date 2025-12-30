"""Base ingester protocol and common types for data ingestion."""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, ClassVar, Self

from pydantic import BaseModel, Field, model_validator

from potluck.core.exceptions import ConfigurationError
from potluck.core.logging import get_logger
from potluck.models.base import BaseEntity, EntityType, SourceType

logger = get_logger(__name__)


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

    @model_validator(mode="after")
    def validate_date_range(self) -> Self:
        """Validate that since is before until when both are specified."""
        if self.since and self.until and self.since > self.until:
            raise ValueError("'since' must be before 'until'")
        return self


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

    Class Attributes:
        SOURCE_TYPE: The SourceType enum value for this ingester.
        FILENAME_PATTERNS: Regex patterns for auto-detecting this source by filename.
        SUPPORTED_ENTITY_TYPES: Entity types this ingester can produce.

    Implementation Pattern:
        For sources with multiple entity types (e.g., Google Takeout with photos
        AND emails), organize your ingester with private methods per entity type:

        class GoogleTakeoutIngester(BaseIngester):
            def ingest(self, path, entity_types, filters):
                if EntityType.MEDIA in entity_types:
                    yield from self._ingest_media(path, filters)
                if EntityType.EMAIL in entity_types:
                    yield from self._ingest_emails(path, filters)

            def _ingest_media(self, path, filters):
                # Media-specific parsing logic
                ...

            def _ingest_emails(self, path, filters):
                # Email-specific parsing logic
                ...

    Usage:
        @register
        class GoogleTakeoutIngester(BaseIngester):
            SOURCE_TYPE = SourceType.GOOGLE_TAKEOUT
            FILENAME_PATTERNS = [r"takeout-.*\\.zip"]
            SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA, EntityType.EMAIL}

            def detect_contents(self, path: Path) -> DetectionResult:
                ...

            def ingest(self, path, entity_types, filters) -> Iterator[BaseEntity]:
                ...
    """

    # Class attributes - must be defined by subclasses
    SOURCE_TYPE: ClassVar[SourceType]
    """The source type enum value for this ingester."""

    FILENAME_PATTERNS: ClassVar[list[str]]
    """Regex patterns matching source file/directory names (e.g., r'Takeout-.*\\.zip')."""

    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]]
    """Entity types this ingester can produce."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Validate that required class attributes are defined by subclasses."""
        super().__init_subclass__(**kwargs)
        # Check required class attributes
        required_attrs = ("SOURCE_TYPE", "FILENAME_PATTERNS", "SUPPORTED_ENTITY_TYPES")
        for attr in required_attrs:
            if not hasattr(cls, attr):
                raise ConfigurationError(f"{cls.__name__} must define class attribute '{attr}'")

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

    @abstractmethod
    def ingest(
        self,
        path: Path,
        entity_types: set[EntityType],
        filters: IngestionFilter | None = None,
    ) -> Iterator[BaseEntity]:
        """Yield entities from the source.

        This is the main ingestion method. It should iterate through the source
        data and yield entities of the requested types, applying any filters.

        For complex sources with multiple entity types, delegate to private
        methods per entity type for cleaner code organization:

            def ingest(self, path, entity_types, filters):
                if EntityType.MEDIA in entity_types:
                    yield from self._ingest_media(path, filters)
                if EntityType.EMAIL in entity_types:
                    yield from self._ingest_emails(path, filters)

        Args:
            path: Path to the extracted source data.
            entity_types: Set of entity types to ingest.
            filters: Optional date range filters.

        Yields:
            BaseEntity instances (Media, ChatMessage, Email, etc.)
        """
        ...

    @classmethod
    def get_instructions(cls) -> str:
        """Load instructions from the ingester's package.

        Instructions are loaded from:
        potluck/ingesters/{source_type}/instructions.md

        Returns:
            Markdown instructions for obtaining this data export,
            or empty string if no instructions file exists.
        """
        try:
            package_name = f"potluck.ingesters.{cls.SOURCE_TYPE.value}"
            resource = files(package_name).joinpath("instructions.md")
            return resource.read_text()
        except FileNotFoundError:
            logger.debug(f"No instructions.md found for {cls.__name__}")
            return ""
        except (AttributeError, TypeError, ModuleNotFoundError) as e:
            logger.debug(f"Could not load instructions for {cls.__name__}: {e}")
            return ""

    @classmethod
    def get_assets_path(cls) -> Path | None:
        """Get path to assets folder for this ingester's instructions.

        Assets (images, etc.) for instructions are stored alongside the ingester:
        potluck/ingesters/{source_type}/assets/

        Returns:
            Path to the assets folder, or None if it doesn't exist.
        """
        try:
            package_name = f"potluck.ingesters.{cls.SOURCE_TYPE.value}"
            assets_resource = files(package_name).joinpath("assets")
            assets_path = Path(str(assets_resource))
            if not assets_path.is_dir():
                logger.debug(f"No assets folder found for {cls.__name__}")
                return None
            return assets_path
        except FileNotFoundError:
            logger.debug(f"No assets folder found for {cls.__name__}")
            return None
        except (AttributeError, TypeError, ModuleNotFoundError) as e:
            logger.debug(f"Could not load assets path for {cls.__name__}: {e}")
            return None
