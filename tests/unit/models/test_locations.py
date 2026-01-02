"""Tests for Location and LocationVisit models."""

from datetime import UTC, datetime

from potluck.models.locations import Location, LocationHistory, LocationType, LocationVisit


class TestLocationModels:
    """Tests for Location and LocationVisit models."""

    def test_location_creation(self) -> None:
        """Location can be created."""
        location = Location(
            source_type="google_takeout",
            name="Home",
            latitude=37.7749,
            longitude=-122.4194,
        )
        assert location.name == "Home"
        assert location.location_type == LocationType.OTHER
        assert location.latitude == 37.7749

    def test_location_type_enum(self) -> None:
        """LocationType enum has expected values."""
        expected = {
            "home",
            "work",
            "school",
            "gym",
            "restaurant",
            "store",
            "transit",
            "airport",
            "hotel",
            "attraction",
            "unknown",
            "other",
        }
        actual = {t.value for t in LocationType}
        assert actual == expected

    def test_location_visit_creation(self) -> None:
        """LocationVisit can be created."""
        visit = LocationVisit(
            source_type="google_takeout",
            latitude=37.7749,
            longitude=-122.4194,
            started_at=datetime.now(UTC),
        )
        assert visit.latitude == 37.7749
        assert visit.duration_minutes is None

    def test_location_history_creation(self) -> None:
        """LocationHistory can be created."""
        history = LocationHistory(
            source_type="google_takeout",
            latitude=37.7749,
            longitude=-122.4194,
            timestamp=datetime.now(UTC),
        )
        assert history.latitude == 37.7749
