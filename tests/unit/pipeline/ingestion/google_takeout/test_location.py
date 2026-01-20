"""Tests for Location History ingestion."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from potluck.models.base import SourceType
from potluck.models.locations import (
    Location,
    LocationHistory,
    LocationType,
    LocationVisit,
)
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.ingestion.google_takeout.location import (
    _name_to_location_type,
    _parse_iso_timestamp,
    _parse_lat_lng,
    _semantic_type_to_place_name,
    ingest_location_visits,
)

# Path to test fixtures
FIXTURES_PATH = Path(__file__).parent.parent.parent.parent.parent / "fixtures" / "google_takeout"


class TestLocationIngestion:
    """Tests for location history ingestion."""

    def test_ingest_from_android_timeline(self) -> None:
        """Ingest location data from Android Timeline.json."""
        entities = list(ingest_location_visits(FIXTURES_PATH))

        # Separate entity types
        visits = [e for e in entities if isinstance(e, LocationVisit)]
        history = [e for e in entities if isinstance(e, LocationHistory)]
        locations = [e for e in entities if isinstance(e, Location)]

        # Should have visits from Android timeline + Takeout timeline
        assert len(visits) > 0
        # Should have timeline path points
        assert len(history) > 0
        # Should have labeled places
        assert len(locations) == 3

    def test_visit_from_home_segment(self) -> None:
        """Home visit segment is parsed correctly."""
        entities = list(ingest_location_visits(FIXTURES_PATH))
        visits = [e for e in entities if isinstance(e, LocationVisit)]

        # Find the home visit
        home_visit = next(
            (v for v in visits if v.place_name == "Home" and v.place_id == "ChIJabc123"),
            None,
        )
        assert home_visit is not None
        assert home_visit.latitude == 40.7128
        assert home_visit.longitude == -74.0060
        assert home_visit.confidence == 0.99
        assert home_visit.duration_minutes == 90  # 1.5 hours

    def test_visit_from_work_segment(self) -> None:
        """Work visit segment is parsed correctly."""
        entities = list(ingest_location_visits(FIXTURES_PATH))
        visits = [e for e in entities if isinstance(e, LocationVisit)]

        # Find the work visit
        work_visit = next(
            (v for v in visits if v.place_name == "Work" and v.place_id == "ChIJdef456"),
            None,
        )
        assert work_visit is not None
        assert work_visit.latitude == 40.7589
        assert work_visit.longitude == -73.9851
        assert work_visit.duration_minutes == 480  # 8 hours

    def test_activity_segment_parsing(self) -> None:
        """Activity segments (travel) are parsed as visits."""
        entities = list(ingest_location_visits(FIXTURES_PATH))
        visits = [e for e in entities if isinstance(e, LocationVisit)]

        # Find the in-vehicle activity
        travel = next((v for v in visits if v.activity_type == "IN_VEHICLE"), None)
        assert travel is not None
        assert travel.latitude == 40.7128
        assert travel.longitude == -74.0060
        assert travel.confidence == 0.85
        assert travel.duration_minutes == 30

    def test_timeline_path_points(self) -> None:
        """Timeline path points are parsed as LocationHistory."""
        entities = list(ingest_location_visits(FIXTURES_PATH))
        history = [e for e in entities if isinstance(e, LocationHistory)]

        # Should have 3 path points
        assert len(history) == 3

        # Check first point
        first_point = next(
            (h for h in history if abs(h.latitude - 40.7589) < 0.001),
            None,
        )
        assert first_point is not None
        assert first_point.source_type == SourceType.GOOGLE_TAKEOUT

    def test_takeout_timeline_edits(self) -> None:
        """Takeout Timeline Edits.json is parsed correctly."""
        # Use just the Timeline subdirectory to test Takeout format
        timeline_dir = FIXTURES_PATH / "Timeline"
        if not timeline_dir.exists():
            return

        entities = list(ingest_location_visits(FIXTURES_PATH))
        visits = [e for e in entities if isinstance(e, LocationVisit)]

        # Find visits from E7 coordinates (Takeout format)
        # These should have accuracy from accuracyMm
        takeout_visits = [v for v in visits if v.accuracy_meters is not None]
        assert len(takeout_visits) >= 2

        # Check E7 coordinate conversion: 407128000 / 10_000_000 = 40.7128
        takeout_visit = next(
            (v for v in takeout_visits if abs(v.latitude - 40.7128) < 0.001),
            None,
        )
        assert takeout_visit is not None
        assert abs(takeout_visit.longitude - (-74.0060)) < 0.001

    def test_labeled_places(self) -> None:
        """Labeled places GeoJSON is parsed correctly."""
        entities = list(ingest_location_visits(FIXTURES_PATH))
        locations = [e for e in entities if isinstance(e, Location)]

        assert len(locations) == 3

        # Find home location
        home = next((loc for loc in locations if loc.name == "Home"), None)
        assert home is not None
        assert home.latitude == 40.7128
        assert home.longitude == -74.0060
        assert home.location_type == LocationType.HOME
        assert "123 Main Street" in (home.address or "")

        # Find work location
        work = next((loc for loc in locations if loc.name == "Work"), None)
        assert work is not None
        assert work.location_type == LocationType.WORK

        # Find gym location
        gym = next((loc for loc in locations if loc.name == "Fitness Center"), None)
        assert gym is not None
        assert gym.location_type == LocationType.GYM

    def test_source_type(self) -> None:
        """All entities have correct source type."""
        entities = list(ingest_location_visits(FIXTURES_PATH))

        for entity in entities:
            assert entity.source_type == SourceType.GOOGLE_TAKEOUT

    def test_date_filter_since(self) -> None:
        """Date filter 'since' excludes earlier data."""
        filters = PipelineFilter(since=datetime(2024, 1, 16, tzinfo=UTC))
        entities = list(ingest_location_visits(FIXTURES_PATH, filters))

        visits = [e for e in entities if isinstance(e, LocationVisit)]
        history = [e for e in entities if isinstance(e, LocationHistory)]

        # Should exclude Jan 15 data
        # Remaining: Jan 16 Takeout data + Jan 20 Android data
        assert len(visits) >= 2  # Takeout visits + Jan 20 visit
        assert len(history) == 0  # All timeline path is from Jan 15

    def test_date_filter_until(self) -> None:
        """Date filter 'until' excludes later data."""
        filters = PipelineFilter(until=datetime(2024, 1, 16, tzinfo=UTC))
        entities = list(ingest_location_visits(FIXTURES_PATH, filters))

        visits = [e for e in entities if isinstance(e, LocationVisit)]

        # Should include only Jan 15 data
        # All Jan 15 visits from Android timeline
        jan_15_visits = [
            v for v in visits if v.started_at and v.started_at.month == 1 and v.started_at.day == 15
        ]
        assert len(jan_15_visits) >= 3  # Home, travel, work

    def test_empty_directory(self) -> None:
        """Empty directory yields no entities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entities = list(ingest_location_visits(Path(tmpdir)))
            assert entities == []


class TestHelperFunctions:
    """Tests for location parsing helper functions."""

    def test_parse_lat_lng_with_degrees(self) -> None:
        """Parse coordinates with degree symbols."""
        lat, lng = _parse_lat_lng("40.7128°, -74.0060°")
        assert lat == 40.7128
        assert lng == -74.0060

    def test_parse_lat_lng_without_degrees(self) -> None:
        """Parse coordinates without degree symbols."""
        lat, lng = _parse_lat_lng("40.7128, -74.0060")
        assert lat == 40.7128
        assert lng == -74.0060

    def test_parse_lat_lng_negative(self) -> None:
        """Parse negative coordinates."""
        lat, lng = _parse_lat_lng("-33.8688°, 151.2093°")
        assert lat == -33.8688
        assert lng == 151.2093

    def test_parse_lat_lng_empty(self) -> None:
        """Empty string returns None tuple."""
        lat, lng = _parse_lat_lng("")
        assert lat is None
        assert lng is None

    def test_parse_lat_lng_invalid(self) -> None:
        """Invalid format returns None tuple."""
        lat, lng = _parse_lat_lng("invalid")
        assert lat is None
        assert lng is None

    def test_parse_iso_timestamp(self) -> None:
        """Parse ISO timestamp with timezone."""
        ts = _parse_iso_timestamp("2024-01-15T08:00:00.000-05:00")
        assert ts is not None
        assert ts.year == 2024
        assert ts.month == 1
        assert ts.day == 15
        assert ts.hour == 13  # 8 AM EST = 13:00 UTC

    def test_parse_iso_timestamp_utc(self) -> None:
        """Parse ISO timestamp with Z suffix."""
        ts = _parse_iso_timestamp("2024-01-15T13:00:00.000Z")
        assert ts is not None
        assert ts.hour == 13

    def test_parse_iso_timestamp_none(self) -> None:
        """None input returns None."""
        assert _parse_iso_timestamp(None) is None

    def test_parse_iso_timestamp_invalid(self) -> None:
        """Invalid format returns None."""
        assert _parse_iso_timestamp("invalid") is None

    def test_semantic_type_to_place_name(self) -> None:
        """Convert semantic types to place names."""
        assert _semantic_type_to_place_name("HOME") == "Home"
        assert _semantic_type_to_place_name("WORK") == "Work"
        assert _semantic_type_to_place_name("SCHOOL") == "School"
        assert _semantic_type_to_place_name("UNKNOWN") is None
        assert _semantic_type_to_place_name("RANDOM") is None

    def test_name_to_location_type(self) -> None:
        """Convert place names to location types."""
        assert _name_to_location_type("Home") == LocationType.HOME
        assert _name_to_location_type("My Home") == LocationType.HOME
        assert _name_to_location_type("Work") == LocationType.WORK
        assert _name_to_location_type("Office") == LocationType.WORK
        assert _name_to_location_type("High School") == LocationType.SCHOOL
        assert _name_to_location_type("City Gym") == LocationType.GYM
        assert _name_to_location_type("Fitness Center") == LocationType.GYM
        assert _name_to_location_type("JFK Airport") == LocationType.AIRPORT
        assert _name_to_location_type("Marriott Hotel") == LocationType.HOTEL
        assert _name_to_location_type("Random Place") == LocationType.OTHER


class TestIntegrationWithStage:
    """Integration tests with GoogleTakeoutStage."""

    def test_stage_executes_location_ingestion(self) -> None:
        """Stage correctly routes to location ingestion."""
        from potluck.models.base import EntityType
        from potluck.pipeline.ingestion.google_takeout import GoogleTakeoutStage

        stage = GoogleTakeoutStage()

        # Execute for location visits only
        entities = list(
            stage.execute(
                FIXTURES_PATH,
                entity_types={EntityType.LOCATION_VISIT},
            )
        )

        # Should get visits, history, and locations
        visits = [e for e in entities if isinstance(e, LocationVisit)]
        history = [e for e in entities if isinstance(e, LocationHistory)]
        locations = [e for e in entities if isinstance(e, Location)]

        assert len(visits) > 0
        assert len(history) > 0
        assert len(locations) > 0
