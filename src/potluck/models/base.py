"""Base SQLModel classes for Potluck entities."""

from datetime import datetime
from enum import Enum
from typing import ClassVar
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel

from potluck.models.utils import IANATimezone, UTCDatetime, utc_now


class SourceType(str, Enum):
    """Enumeration of supported data ingestion sources."""

    GOOGLE_TAKEOUT = "google_takeout"
    REDDIT = "reddit"
    WHATSAPP = "whatsapp"
    YNAB = "ynab"
    GENERIC = "generic"  # Bulk import of generic files (images, markdown, MBOX)
    MANUAL = "manual"  # User-created content within Potluck (notes, annotations)


class EntityType(str, Enum):
    """Types of entities that can be ingested, linked, and searched.

    This is the canonical enum for entity types used across ingestion,
    entity linking, and search functionality.
    """

    MEDIA = "media"
    CHAT_MESSAGE = "chat_message"
    EMAIL = "email"
    SOCIAL_POST = "social_post"
    SOCIAL_COMMENT = "social_comment"
    KNOWLEDGE_NOTE = "knowledge_note"
    CALENDAR_EVENT = "calendar_event"
    TRANSACTION = "transaction"
    LOCATION = "location"
    LOCATION_VISIT = "location_visit"
    BROWSING_HISTORY = "browsing_history"
    BOOKMARK = "bookmark"
    PERSON = "person"
    TAG = "tag"


class TimestampPrecision(str, Enum):
    """Precision level for occurred_at timestamps."""

    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    HOUR = "hour"
    MINUTE = "minute"
    SECOND = "second"


class SimpleEntity(SQLModel):
    """Minimal base class for auxiliary entities.

    Provides id, created_at, and updated_at for entities that don't need
    full source tracking (e.g., link tables, embeddings, participants).

    Search Configuration (class variables):
        __searchable__: Whether this entity type supports search. Default False.
        __search_exclude_fields__: Fields to exclude from auto-discovered text search.
        __search_priority_fields__: Fields to weight higher in FTS (weight 'A').
        __search_date_fields__: Date fields for date-range filtering.
    """

    __abstract__: ClassVar[bool] = True

    # Search configuration - subclasses override these
    __searchable__: ClassVar[bool] = False
    __search_exclude_fields__: ClassVar[set[str]] = set()
    __search_priority_fields__: ClassVar[set[str]] = set()
    __search_date_fields__: ClassVar[set[str]] = set()

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the entity",
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        description="When the entity was created in the database",
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column_kwargs={"onupdate": utc_now},
        description="When the entity was last updated",
    )

    def to_search_repr(self) -> str:
        """Generate a human-readable text representation for search results.

        Subclasses can override this for entity-specific formatting.
        Default implementation returns the entity type name.

        Returns:
            Text representation suitable for displaying in search results.
        """
        return self.__class__.__name__


class BaseEntity(SimpleEntity):
    """Base class for all Potluck entities.

    Inherits id, created_at, updated_at, and search configuration from SimpleEntity.
    Adds source tracking and content hashing for deduplication.
    """

    __abstract__: ClassVar[bool] = True

    source_type: SourceType = Field(
        description="The source system this entity was imported from",
    )
    source_id: str | None = Field(
        default=None,
        description="Original identifier from the source system",
    )
    content_hash: str | None = Field(
        default=None,
        index=True,
        description="SHA256 hash of content for deduplication",
    )

    def to_search_repr(self) -> str:
        """Generate a human-readable text representation for search results.

        Override of SimpleEntity's method to include source_type.

        Returns:
            Text representation suitable for displaying in search results.
        """
        entity_type = self.__class__.__name__
        return f"[{self.source_type.value}] {entity_type}"


class TimestampedEntity(BaseEntity):
    """Base class for entities with a meaningful occurrence time.

    Extends BaseEntity with fields for when the entity actually occurred
    (as opposed to when it was imported), with configurable precision.

    The occurred_at field is always stored as UTC. If the original timestamp
    was in a different timezone, store that in source_timezone for display.
    """

    __abstract__: ClassVar[bool] = True

    occurred_at: UTCDatetime = Field(
        default=None,
        index=True,
        description="When this entity actually occurred in UTC (e.g., photo taken, message sent)",
    )
    occurred_at_precision: TimestampPrecision = Field(
        default=TimestampPrecision.SECOND,
        description="Precision of the occurred_at timestamp",
    )
    source_timezone: IANATimezone = Field(
        default=None,
        description="IANA timezone of the original timestamp (e.g., 'America/New_York')",
    )


class GeolocatedEntity(TimestampedEntity):
    """Base class for entities with geographic location.

    Extends TimestampedEntity with latitude, longitude, altitude, and optional
    location name for entities that have a physical location.
    """

    __abstract__: ClassVar[bool] = True

    latitude: float | None = Field(
        default=None,
        ge=-90,
        le=90,
        description="Latitude coordinate (-90 to 90)",
    )
    longitude: float | None = Field(
        default=None,
        ge=-180,
        le=180,
        description="Longitude coordinate (-180 to 180)",
    )
    altitude: float | None = Field(
        default=None,
        description="Altitude in meters above sea level",
    )
    location_name: str | None = Field(
        default=None,
        description="Human-readable location name (e.g., 'New York, NY')",
    )

    @property
    def has_location(self) -> bool:
        """Check if this entity has valid coordinates."""
        return self.latitude is not None and self.longitude is not None
