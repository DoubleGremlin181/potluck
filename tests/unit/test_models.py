"""Tests for base models."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlmodel import SQLModel

from potluck.models.base import (
    BaseEntity,
    GeolocatedEntity,
    SourceType,
    TimestampedEntity,
    TimestampPrecision,
)


def utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(UTC)


class ConcreteBase(BaseEntity, table=True):
    """Concrete implementation of BaseEntity for testing."""

    __tablename__ = "test_base"


class ConcreteTimestamped(TimestampedEntity, table=True):
    """Concrete implementation of TimestampedEntity for testing."""

    __tablename__ = "test_timestamped"


class ConcreteGeolocated(GeolocatedEntity, table=True):
    """Concrete implementation of GeolocatedEntity for testing."""

    __tablename__ = "test_geolocated"


class TestSourceType:
    """Tests for SourceType enum."""

    def test_all_sources_defined(self) -> None:
        """All expected source types are defined."""
        expected = {"google_takeout", "reddit", "whatsapp", "ynab", "generic", "manual"}
        actual = {s.value for s in SourceType}
        assert actual == expected

    def test_source_type_is_string(self) -> None:
        """SourceType values are strings."""
        assert SourceType.GOOGLE_TAKEOUT.value == "google_takeout"
        assert isinstance(SourceType.REDDIT.value, str)


class TestTimestampPrecision:
    """Tests for TimestampPrecision enum."""

    def test_precision_levels(self) -> None:
        """All precision levels are defined."""
        expected = {"year", "month", "day", "hour", "minute", "second"}
        actual = {p.value for p in TimestampPrecision}
        assert actual == expected


class TestBaseEntity:
    """Tests for BaseEntity model."""

    def test_id_is_uuid(self) -> None:
        """Entity id is a UUID."""
        entity = ConcreteBase(source_type=SourceType.MANUAL)
        assert isinstance(entity.id, UUID)

    def test_id_auto_generated(self) -> None:
        """Each entity gets a unique id."""
        e1 = ConcreteBase(source_type=SourceType.MANUAL)
        e2 = ConcreteBase(source_type=SourceType.MANUAL)
        assert e1.id != e2.id

    def test_timestamps_auto_set(self) -> None:
        """created_at and updated_at are auto-set."""
        entity = ConcreteBase(source_type=SourceType.MANUAL)
        assert isinstance(entity.created_at, datetime)
        assert isinstance(entity.updated_at, datetime)

    def test_source_type_required(self) -> None:
        """source_type is a required field."""
        entity = ConcreteBase(source_type=SourceType.GOOGLE_TAKEOUT)
        assert entity.source_type == SourceType.GOOGLE_TAKEOUT

    def test_optional_fields_default_none(self) -> None:
        """Optional fields default to None."""
        entity = ConcreteBase(source_type=SourceType.MANUAL)
        assert entity.source_id is None
        assert entity.content_hash is None

    def test_content_hash_can_be_set(self) -> None:
        """content_hash can be set for deduplication."""
        hash_value = "abc123def456"
        entity = ConcreteBase(source_type=SourceType.MANUAL, content_hash=hash_value)
        assert entity.content_hash == hash_value

    def test_is_sqlmodel(self) -> None:
        """BaseEntity inherits from SQLModel."""
        assert issubclass(BaseEntity, SQLModel)


class TestTimestampedEntity:
    """Tests for TimestampedEntity model."""

    def test_inherits_base_fields(self) -> None:
        """TimestampedEntity has all BaseEntity fields."""
        entity = ConcreteTimestamped(source_type=SourceType.MANUAL)
        assert hasattr(entity, "id")
        assert hasattr(entity, "created_at")
        assert hasattr(entity, "source_type")
        assert hasattr(entity, "content_hash")

    def test_occurred_at_optional(self) -> None:
        """occurred_at defaults to None."""
        entity = ConcreteTimestamped(source_type=SourceType.MANUAL)
        assert entity.occurred_at is None

    def test_occurred_at_can_be_set(self) -> None:
        """occurred_at can be set to a datetime."""
        now = utc_now()
        entity = ConcreteTimestamped(source_type=SourceType.MANUAL, occurred_at=now)
        assert entity.occurred_at == now

    def test_precision_default(self) -> None:
        """occurred_at_precision defaults to SECOND."""
        entity = ConcreteTimestamped(source_type=SourceType.MANUAL)
        assert entity.occurred_at_precision == TimestampPrecision.SECOND

    def test_precision_can_be_set(self) -> None:
        """occurred_at_precision can be customized."""
        entity = ConcreteTimestamped(
            source_type=SourceType.MANUAL,
            occurred_at_precision=TimestampPrecision.DAY,
        )
        assert entity.occurred_at_precision == TimestampPrecision.DAY


class TestGeolocatedEntity:
    """Tests for GeolocatedEntity model."""

    def test_inherits_timestamped_fields(self) -> None:
        """GeolocatedEntity has all TimestampedEntity fields."""
        entity = ConcreteGeolocated(source_type=SourceType.MANUAL)
        assert hasattr(entity, "id")
        assert hasattr(entity, "occurred_at")
        assert hasattr(entity, "occurred_at_precision")

    def test_location_fields_optional(self) -> None:
        """Location fields default to None."""
        entity = ConcreteGeolocated(source_type=SourceType.MANUAL)
        assert entity.latitude is None
        assert entity.longitude is None
        assert entity.location_name is None

    def test_has_location_false_when_no_coords(self) -> None:
        """has_location is False when coordinates are None."""
        entity = ConcreteGeolocated(source_type=SourceType.MANUAL)
        assert entity.has_location is False

    def test_has_location_false_when_partial_coords(self) -> None:
        """has_location is False when only one coordinate is set."""
        entity = ConcreteGeolocated(source_type=SourceType.MANUAL, latitude=40.7128)
        assert entity.has_location is False

    def test_has_location_true_when_both_coords(self) -> None:
        """has_location is True when both coordinates are set."""
        entity = ConcreteGeolocated(
            source_type=SourceType.MANUAL,
            latitude=40.7128,
            longitude=-74.0060,
        )
        assert entity.has_location is True

    def test_latitude_validation(self) -> None:
        """Latitude must be between -90 and 90."""
        # Valid latitudes work
        ConcreteGeolocated(source_type=SourceType.MANUAL, latitude=0)
        ConcreteGeolocated(source_type=SourceType.MANUAL, latitude=90)
        ConcreteGeolocated(source_type=SourceType.MANUAL, latitude=-90)

        # Invalid latitudes raise ValidationError when validated
        with pytest.raises(ValidationError):
            ConcreteGeolocated.model_validate({"source_type": SourceType.MANUAL, "latitude": 91})
        with pytest.raises(ValidationError):
            ConcreteGeolocated.model_validate({"source_type": SourceType.MANUAL, "latitude": -91})

    def test_longitude_validation(self) -> None:
        """Longitude must be between -180 and 180."""
        # Valid longitudes work
        ConcreteGeolocated(source_type=SourceType.MANUAL, longitude=0)
        ConcreteGeolocated(source_type=SourceType.MANUAL, longitude=180)
        ConcreteGeolocated(source_type=SourceType.MANUAL, longitude=-180)

        # Invalid longitudes raise ValidationError when validated
        with pytest.raises(ValidationError):
            ConcreteGeolocated.model_validate({"source_type": SourceType.MANUAL, "longitude": 181})
        with pytest.raises(ValidationError):
            ConcreteGeolocated.model_validate({"source_type": SourceType.MANUAL, "longitude": -181})

    def test_location_name_can_be_set(self) -> None:
        """location_name can store human-readable location."""
        entity = ConcreteGeolocated(
            source_type=SourceType.MANUAL,
            location_name="New York, NY",
        )
        assert entity.location_name == "New York, NY"
