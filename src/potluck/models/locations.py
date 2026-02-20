"""Location and visit tracking models."""

from datetime import datetime
from enum import Enum
from typing import ClassVar
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import Field, Relationship, SQLModel

from potluck.core.constants import MULTIMODAL_EMBEDDING_DIM, TEXT_EMBEDDING_DIM
from potluck.models.base import IngestableEntity, SourceType, enum_field
from potluck.models.utils import utc_now


class LocationType(str, Enum):
    """Type of saved location."""

    HOME = "home"
    WORK = "work"
    SCHOOL = "school"
    GYM = "gym"
    RESTAURANT = "restaurant"
    STORE = "store"
    TRANSIT = "transit"
    AIRPORT = "airport"
    HOTEL = "hotel"
    ATTRACTION = "attraction"
    UNKNOWN = "unknown"
    OTHER = "other"


class Location(SQLModel, IngestableEntity, table=True):
    """Named location with coordinates.

    Represents labeled places from Google Maps, manual entries, etc.
    Inherits from IngestableEntity to allow proper typing for ingester yields.
    """

    __tablename__ = "locations"

    # Forbid extra fields to catch bugs early
    model_config = {"extra": "forbid"}

    # Search configuration - name is priority, address/city auto-discovered
    __searchable__: ClassVar[bool] = True
    __search_exclude_fields__: ClassVar[set[str]] = set()
    __search_priority_fields__: ClassVar[set[str]] = {"name"}
    __search_date_fields__: ClassVar[set[str]] = {"created_at"}

    def to_text_repr(self) -> str:
        """Return text representation with ID for LLM context."""
        location_parts = [self.name]
        if self.city:
            location_parts.append(self.city)
        if self.country:
            location_parts.append(self.country)
        return f"Location (id: {self.id}): {', '.join(location_parts)}"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the location",
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        description="When the location was created in the database",
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column_kwargs={"onupdate": utc_now},
        description="When the location was last updated",
    )
    source_type: SourceType = enum_field(
        description="Source of the location data",
    )
    source_id: str | None = Field(
        default=None,
        description="Original identifier from the source system (e.g., place_id)",
    )
    content_hash: str | None = Field(
        default=None,
        index=True,
        description="SHA256 hash of content for deduplication",
    )

    # Person association (for storing someone else's home/work address)
    person_id: UUID | None = Field(
        default=None,
        foreign_key="people.id",
        index=True,
        description="Person this location belongs to (e.g., Jack's home address)",
    )

    # Location metadata
    name: str = Field(
        description="Location name (e.g., 'Home', 'Starbucks on Main St')",
    )
    location_type: LocationType = enum_field(
        default=LocationType.OTHER,
        description="Category of location",
    )

    # Coordinates
    latitude: float = Field(
        ge=-90,
        le=90,
        description="Latitude coordinate",
    )
    longitude: float = Field(
        ge=-180,
        le=180,
        description="Longitude coordinate",
    )

    # Address information
    address: str | None = Field(
        default=None,
        description="Full formatted address",
    )
    street: str | None = Field(
        default=None,
        description="Street address",
    )
    city: str | None = Field(
        default=None,
        index=True,
        description="City name",
    )
    state: str | None = Field(
        default=None,
        description="State/province",
    )
    country: str | None = Field(
        default=None,
        index=True,
        description="Country name",
    )
    postal_code: str | None = Field(
        default=None,
        description="Postal/ZIP code",
    )

    # Place details (from Google Maps)
    place_id: str | None = Field(
        default=None,
        index=True,
        description="Google Place ID",
    )
    google_maps_url: str | None = Field(
        default=None,
        description="URL to Google Maps",
    )
    phone: str | None = Field(
        default=None,
        description="Phone number if business",
    )
    website: str | None = Field(
        default=None,
        description="Website URL",
    )

    # User notes
    notes: str | None = Field(
        default=None,
        description="User notes about this location",
    )

    # Embeddings for semantic search
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(TEXT_EMBEDDING_DIM)),
        description="Text embedding for text-to-text semantic search",
    )
    multimodal_embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(MULTIMODAL_EMBEDDING_DIM)),
        description="Multimodal embedding for text-to-image cross-modal search",
    )

    # Full-text search vector (auto-populated by database trigger)
    search_vector: str | None = Field(
        default=None,
        sa_column=Column(TSVECTOR),
        description="FTS vector for keyword search (auto-populated by trigger)",
    )

    # Relationships
    visits: list["LocationVisit"] = Relationship(back_populates="location")


class LocationVisit(SQLModel, IngestableEntity, table=True):
    """Visit to a location with timing information.

    Tracks when the user was at a specific location.
    Inherits from IngestableEntity to allow proper typing for ingester yields.
    """

    __tablename__ = "location_visits"

    # Forbid extra fields to catch bugs early
    model_config = {"extra": "forbid"}

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the visit",
    )
    location_id: UUID | None = Field(
        default=None,
        foreign_key="locations.id",
        index=True,
        description="The location visited (if matched)",
    )
    source_type: SourceType = enum_field(
        description="Source of the visit data",
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

    # Coordinates (stored separately as location may not be matched)
    latitude: float = Field(
        ge=-90,
        le=90,
        description="Latitude coordinate",
    )
    longitude: float = Field(
        ge=-180,
        le=180,
        description="Longitude coordinate",
    )
    accuracy_meters: float | None = Field(
        default=None,
        description="Location accuracy in meters",
    )

    # Timing
    started_at: datetime = Field(
        index=True,
        description="When the visit started",
    )
    ended_at: datetime | None = Field(
        default=None,
        description="When the visit ended",
    )
    duration_minutes: int | None = Field(
        default=None,
        description="Duration of visit in minutes",
    )
    # occurred_at aliases started_at for consistent filtering/search
    occurred_at: datetime | None = Field(
        default=None,
        index=True,
        description="When the visit occurred (same as started_at, for search consistency)",
    )

    # Place information (if location not matched)
    place_name: str | None = Field(
        default=None,
        description="Name of the place visited",
    )
    address: str | None = Field(
        default=None,
        description="Address if known",
    )
    place_id: str | None = Field(
        default=None,
        description="Google Place ID",
    )

    # Activity detected
    activity_type: str | None = Field(
        default=None,
        description="Detected activity (walking, driving, etc.)",
    )
    confidence: float | None = Field(
        default=None,
        description="Confidence in the activity detection",
    )

    # Relationships
    location: "Location" = Relationship(back_populates="visits")


class LocationHistory(SQLModel, IngestableEntity, table=True):
    """Raw location history point from timeline data.

    Stores individual location pings from Google Timeline, etc.
    Inherits from IngestableEntity to allow proper typing for ingester yields.
    """

    __tablename__ = "location_history"

    # Forbid extra fields to catch bugs early
    model_config = {"extra": "forbid"}

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier",
    )
    source_type: SourceType = enum_field(
        description="Source of the location data",
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

    # Coordinates
    latitude: float = Field(
        ge=-90,
        le=90,
        description="Latitude coordinate",
    )
    longitude: float = Field(
        ge=-180,
        le=180,
        description="Longitude coordinate",
    )
    altitude: float | None = Field(
        default=None,
        description="Altitude in meters",
    )

    # Accuracy
    accuracy_meters: float | None = Field(
        default=None,
        description="Horizontal accuracy in meters",
    )
    vertical_accuracy: float | None = Field(
        default=None,
        description="Vertical accuracy in meters",
    )

    # Timing
    timestamp: datetime = Field(
        index=True,
        description="When the location was recorded",
    )
    # occurred_at aliases timestamp for consistent filtering/search
    occurred_at: datetime | None = Field(
        default=None,
        index=True,
        description="When recorded (same as timestamp, for search consistency)",
    )

    # Velocity (if moving)
    speed_mps: float | None = Field(
        default=None,
        description="Speed in meters per second",
    )
    heading: float | None = Field(
        default=None,
        description="Heading in degrees (0-360)",
    )

    # Source device
    device_id: str | None = Field(
        default=None,
        description="Device identifier",
    )
    source: str | None = Field(
        default=None,
        description="Location source (GPS, WiFi, cell)",
    )
