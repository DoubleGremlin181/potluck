"""Location and visit tracking models."""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from potluck.models.base import _utc_now


class LocationType(str, Enum):
    """Type of saved location."""

    # User-labeled locations
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

    # Google Timeline inferred locations (from semantic segments)
    INFERRED_HOME = "inferred_home"
    INFERRED_WORK = "inferred_work"
    SEARCHED_ADDRESS = "searched_address"
    ALIASED_LOCATION = "aliased_location"

    # Fallback
    UNKNOWN = "unknown"
    OTHER = "other"


class Location(SQLModel, table=True):
    """Named location with coordinates.

    Represents labeled places from Google Maps, manual entries, etc.
    """

    __tablename__ = "locations"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the location",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the location was created in the database",
    )
    updated_at: datetime = Field(
        default_factory=_utc_now,
        sa_column_kwargs={"onupdate": _utc_now},
        description="When the location was last updated",
    )
    source_type: str = Field(
        description="Source of the location data",
    )

    # Location metadata
    name: str = Field(
        description="Location name (e.g., 'Home', 'Starbucks on Main St')",
    )
    location_type: LocationType = Field(
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

    # Relationships
    visits: list["LocationVisit"] = Relationship(back_populates="location")


class LocationVisit(SQLModel, table=True):
    """Visit to a location with timing information.

    Tracks when the user was at a specific location.
    """

    __tablename__ = "location_visits"

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
    source_type: str = Field(
        description="Source of the visit data",
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


class LocationHistory(SQLModel, table=True):
    """Raw location history point from timeline data.

    Stores individual location pings from Google Timeline, etc.
    """

    __tablename__ = "location_history"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier",
    )
    source_type: str = Field(
        description="Source of the location data",
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
