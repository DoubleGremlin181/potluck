"""Base SQLModel classes for Potluck entities."""

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel

from potluck.models.utils import IANATimezone, UTCDatetime, utc_now


class EnumStr(TypeDecorator[str]):
    """SQLAlchemy type that stores str-Enums as VARCHAR and coerces back on load.

    Without this, SQLAlchemy returns plain strings from the DB for VARCHAR columns.
    Code that calls `entity.status.value` or compares `entity.status == SomeEnum.X`
    would break or behave inconsistently depending on whether the object was freshly
    created (enum) vs loaded from DB (string).

    This type transparently handles both directions:
    - Write: enum → string value for storage
    - Read:  string → enum instance on load
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: type[Enum]) -> None:
        super().__init__()
        self.enum_class = enum_class

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:  # noqa: ANN401
        if value is None:
            return None
        if isinstance(value, self.enum_class):
            return value.value  # type: ignore[no-any-return]
        # Validate that the string is a valid enum value before storing
        self.enum_class(value)
        return str(value)

    def process_result_value(self, value: Any, dialect: Any) -> Any:  # noqa: ANN401
        if value is None:
            return None
        return self.enum_class(value)


def enum_field(enum_cls: type[Enum], /, *, default: Any = ..., **kwargs: Any) -> Any:
    """Field helper for str-Enum columns that stores as VARCHAR, not PG native enum.

    Uses EnumStr TypeDecorator to auto-coerce DB strings back into enum instances,
    so code like ``entity.status.value`` works regardless of whether the object
    was freshly created or loaded from the database.

    Args:
        enum_cls: The enum class for this field (e.g., ``SourceType``, ``ImportStatus``).
        default: Optional default value for the field.
        **kwargs: Additional keyword arguments passed to ``Field()``.
    """
    # TypeDecorator instances work with sa_type at runtime; mypy expects a type
    sa_type: Any = EnumStr(enum_cls)
    if default is ...:
        return Field(sa_type=sa_type, **kwargs)
    return Field(default=default, sa_type=sa_type, **kwargs)


class IngestableEntity:
    """Marker base class for all types that can be yielded by ingesters.

    This class provides type safety for ingestion stage return types.
    All entities that ingesters yield should inherit from this class,
    either directly or through SimpleEntity/BaseEntity.

    Usage:
        - SimpleEntity and its subclasses inherit this automatically
        - Standalone SQLModel classes (ChatThread, Location, etc.) should
          also inherit from this for proper typing
    """

    pass


class SourceType(str, Enum):
    """Enumeration of supported data ingestion sources."""

    GOOGLE_TAKEOUT = "google_takeout"
    ANDROID_TIMELINE = "android_timeline"  # Android Timeline export (Timeline.json)
    REDDIT = "reddit"
    WHATSAPP = "whatsapp"
    YNAB = "ynab"
    GENERIC = "generic"  # Generic file-based imports (images, text, MBOX, etc.)
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
    DOCUMENT = "document"
    CALENDAR_EVENT = "calendar_event"
    TRANSACTION = "transaction"
    LOCATION = "location"
    LOCATION_VISIT = "location_visit"
    BROWSING_HISTORY = "browsing_history"
    BOOKMARK = "bookmark"
    SOCIAL_FOLLOW = "social_follow"
    BUDGET = "budget"
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


class SimpleEntity(SQLModel, IngestableEntity):
    """Minimal base class for auxiliary entities.

    Provides id, created_at, and updated_at for entities that don't need
    full source tracking (e.g., link tables, embeddings, participants).

    Inherits from IngestableEntity to allow proper typing for ingester yields.

    Search Configuration (class variables):
        __searchable__: Whether this entity type supports search. Default False.
        __search_exclude_fields__: Fields to exclude from auto-discovered text search.
        __search_priority_fields__: Fields to weight higher in FTS (weight 'A').
        __search_date_fields__: Date fields for date-range filtering.
    """

    __abstract__: ClassVar[bool] = True

    # Forbid extra fields to catch bugs early (e.g., typos, removed fields)
    model_config = {"extra": "forbid"}

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

    def to_text_repr(self) -> str:
        """Return a text representation useful for LLMs and related content lookup.

        This representation is used for:
        - Search result display
        - LLM context (helping the model understand and reference entities)
        - Finding related content via IDs and metadata

        Format guidelines for subclass implementations:
        - Start with entity type and ID: "Photo (id: abc123)"
        - Include primary identifier/title
        - Include key relationships with IDs: "person: John (id: xyz789)"
        - Include temporal info: "date: 2024-01-15"
        - Include location if relevant: "location: Beach House (id: loc456)"
        - Include tags if present

        The goal is to provide enough context that an LLM can:
        1. Understand what this entity is
        2. Reference it by ID in follow-up queries
        3. Find related entities via included relationship IDs

        Returns:
            Human-readable text with IDs for entity lookup.
        """
        return f"{self.__class__.__name__} (id: {self.id})"


class BaseEntity(SimpleEntity):
    """Base class for all Potluck entities.

    Inherits id, created_at, updated_at, and search configuration from SimpleEntity.
    Adds source tracking and content hashing for deduplication.
    """

    __abstract__: ClassVar[bool] = True

    source_type: SourceType = enum_field(
        SourceType,
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

    def to_text_repr(self) -> str:
        """Return a text representation useful for LLMs and related content lookup.

        Override of SimpleEntity's method to include source_type.
        See SimpleEntity.to_text_repr for format guidelines.

        Returns:
            Human-readable text with IDs for entity lookup.
        """
        entity_type = self.__class__.__name__
        return f"{entity_type} (id: {self.id}) | source: {self.source_type.value}"


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
    occurred_at_precision: TimestampPrecision = enum_field(
        TimestampPrecision,
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
