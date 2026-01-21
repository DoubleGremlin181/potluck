"""Tests for Google Takeout Location History ingestion.

Tests the location.py module which handles:
- Timeline Edits.json (Google Takeout sparse timeline data)
- Labeled places.json (GeoJSON format)

Note: Android Timeline.json is handled by the separate AndroidTimelineStage.
"""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from potluck.models.base import SourceType
from potluck.models.locations import (
    Location,
    LocationType,
    LocationVisit,
)
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.ingestion.google_takeout.location import (
    _name_to_location_type,
    _parse_iso_timestamp,
    ingest_location_visits,
)

# Path to test fixtures
FIXTURES_PATH = Path(__file__).parent.parent.parent.parent.parent / "fixtures" / "google_takeout"


class TestTakeoutTimelineEdits:
    """Tests for Google Takeout Timeline Edits.json parsing."""

    def test_ingest_timeline_edits(self) -> None:
        """Ingest location visits from Timeline Edits.json."""
        entities = list(ingest_location_visits(FIXTURES_PATH))

        visits = [e for e in entities if isinstance(e, LocationVisit)]

        # Should have 2 visits from Timeline Edits.json
        assert len(visits) == 2

    def test_first_edit_coordinates(self) -> None:
        """First timeline edit has correct E7 coordinate conversion."""
        entities = list(ingest_location_visits(FIXTURES_PATH))
        visits = [e for e in entities if isinstance(e, LocationVisit)]

        # 407128000 / 10_000_000 = 40.7128
        first_visit = next(
            (v for v in visits if abs(v.latitude - 40.7128) < 0.0001),
            None,
        )
        assert first_visit is not None
        assert abs(first_visit.longitude - (-74.0060)) < 0.0001
        assert first_visit.accuracy_meters == 10.0  # 10000mm -> 10m

    def test_second_edit_coordinates(self) -> None:
        """Second timeline edit has correct coordinates."""
        entities = list(ingest_location_visits(FIXTURES_PATH))
        visits = [e for e in entities if isinstance(e, LocationVisit)]

        # 407589000 / 10_000_000 = 40.7589
        second_visit = next(
            (v for v in visits if abs(v.latitude - 40.7589) < 0.0001),
            None,
        )
        assert second_visit is not None
        assert abs(second_visit.longitude - (-73.9851)) < 0.0001
        assert second_visit.accuracy_meters == 15.0  # 15000mm -> 15m

    def test_source_type(self) -> None:
        """All visits have GOOGLE_TAKEOUT source type."""
        entities = list(ingest_location_visits(FIXTURES_PATH))
        visits = [e for e in entities if isinstance(e, LocationVisit)]

        for visit in visits:
            assert visit.source_type == SourceType.GOOGLE_TAKEOUT

    def test_occurred_at_set(self) -> None:
        """Visits have occurred_at field set for search consistency."""
        entities = list(ingest_location_visits(FIXTURES_PATH))
        visits = [e for e in entities if isinstance(e, LocationVisit)]

        for visit in visits:
            assert visit.occurred_at is not None
            assert visit.occurred_at == visit.started_at


class TestLabeledPlaces:
    """Tests for labeled places GeoJSON parsing."""

    def test_ingest_labeled_places(self) -> None:
        """Ingest labeled places from GeoJSON."""
        entities = list(ingest_location_visits(FIXTURES_PATH))
        locations = [e for e in entities if isinstance(e, Location)]

        # Should have 3 labeled places
        assert len(locations) == 3

    def test_home_location(self) -> None:
        """Home labeled place is parsed correctly."""
        entities = list(ingest_location_visits(FIXTURES_PATH))
        locations = [e for e in entities if isinstance(e, Location)]

        home = next((loc for loc in locations if loc.name == "Home"), None)
        assert home is not None
        assert home.latitude == 40.7128
        assert home.longitude == -74.0060
        assert home.location_type == LocationType.HOME
        assert "123 Main Street" in (home.address or "")
        assert home.source_type == SourceType.GOOGLE_TAKEOUT

    def test_work_location(self) -> None:
        """Work labeled place is parsed correctly."""
        entities = list(ingest_location_visits(FIXTURES_PATH))
        locations = [e for e in entities if isinstance(e, Location)]

        work = next((loc for loc in locations if loc.name == "Work"), None)
        assert work is not None
        assert work.latitude == 40.7589
        assert work.longitude == -73.9851
        assert work.location_type == LocationType.WORK

    def test_gym_location(self) -> None:
        """Fitness Center labeled place is parsed correctly."""
        entities = list(ingest_location_visits(FIXTURES_PATH))
        locations = [e for e in entities if isinstance(e, Location)]

        gym = next((loc for loc in locations if loc.name == "Fitness Center"), None)
        assert gym is not None
        assert gym.location_type == LocationType.GYM


class TestDateFilters:
    """Tests for date range filtering."""

    def test_since_filter(self) -> None:
        """Date filter 'since' excludes earlier data."""
        # Timeline Edits are from 2024-01-16
        filters = PipelineFilter(since=datetime(2024, 1, 16, 15, 0, tzinfo=UTC))
        entities = list(ingest_location_visits(FIXTURES_PATH, filters))

        visits = [e for e in entities if isinstance(e, LocationVisit)]

        # Should only get the 15:30 visit, not the 12:00 visit
        assert len(visits) == 1
        assert visits[0].started_at.hour == 15

    def test_until_filter(self) -> None:
        """Date filter 'until' excludes later data."""
        # Timeline Edits are from 2024-01-16
        filters = PipelineFilter(until=datetime(2024, 1, 16, 13, 0, tzinfo=UTC))
        entities = list(ingest_location_visits(FIXTURES_PATH, filters))

        visits = [e for e in entities if isinstance(e, LocationVisit)]

        # Should only get the 12:00 visit, not the 15:30 visit
        assert len(visits) == 1
        assert visits[0].started_at.hour == 12


class TestHelperFunctions:
    """Tests for location parsing helper functions."""

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

    def test_name_to_location_type(self) -> None:
        """Convert place names to location types."""
        assert _name_to_location_type("Home") == LocationType.HOME
        assert _name_to_location_type("My Home") == LocationType.HOME
        assert _name_to_location_type("Work") == LocationType.WORK
        assert _name_to_location_type("Office") == LocationType.WORK
        assert _name_to_location_type("My Work") == LocationType.WORK
        assert _name_to_location_type("High School") == LocationType.SCHOOL
        assert _name_to_location_type("City Gym") == LocationType.GYM
        assert _name_to_location_type("Fitness Center") == LocationType.GYM
        assert _name_to_location_type("JFK Airport") == LocationType.AIRPORT
        assert _name_to_location_type("Marriott Hotel") == LocationType.HOTEL
        assert _name_to_location_type("Random Place") == LocationType.OTHER


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_directory(self) -> None:
        """Empty directory yields no entities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entities = list(ingest_location_visits(Path(tmpdir)))
            assert entities == []

    def test_missing_timeline_dir(self) -> None:
        """Missing Timeline directory is handled gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create only Maps directory, no Timeline
            maps_dir = Path(tmpdir) / "Takeout" / "Maps" / "My labeled places"
            maps_dir.mkdir(parents=True)

            # Create empty labeled places
            geojson = maps_dir / "Labeled places.json"
            geojson.write_text('{"features": []}')

            entities = list(ingest_location_visits(Path(tmpdir)))
            # Should not crash, just no visits from timeline
            assert isinstance(entities, list)

    def test_missing_labeled_places_dir(self) -> None:
        """Missing labeled places directory is handled gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create only Timeline directory, no Maps
            timeline_dir = Path(tmpdir) / "Takeout" / "Timeline"
            timeline_dir.mkdir(parents=True)

            # Create empty timeline edits
            edits = timeline_dir / "Timeline Edits.json"
            edits.write_text('{"timelineEdits": []}')

            entities = list(ingest_location_visits(Path(tmpdir)))
            # Should not crash, just no locations from labeled places
            assert isinstance(entities, list)

    def test_nested_point_format(self) -> None:
        """Parse Timeline Edits with nested point structure.

        Real Google Takeout exports (as of late 2024) use a nested format:
        position.point.latE7 instead of position.latE7
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            timeline_dir = Path(tmpdir) / "Takeout" / "Timeline"
            timeline_dir.mkdir(parents=True)

            # Create timeline edits with nested point structure
            edits = timeline_dir / "Timeline Edits.json"
            edits.write_text("""{
              "timelineEdits": [{
                "deviceId": "-1234567890",
                "rawSignal": {
                  "signal": {
                    "position": {
                      "point": {
                        "latE7": 377746689,
                        "lngE7": -1224077150
                      },
                      "accuracyMm": 100000,
                      "timestamp": "2025-01-04T05:46:15.808Z"
                    }
                  }
                }
              }]
            }""")

            entities = list(ingest_location_visits(Path(tmpdir)))
            visits = [e for e in entities if isinstance(e, LocationVisit)]

            assert len(visits) == 1
            visit = visits[0]

            # 377746689 / 10_000_000 = 37.7746689
            assert abs(visit.latitude - 37.7746689) < 0.0001
            # -1224077150 / 10_000_000 = -122.407715
            assert abs(visit.longitude - (-122.407715)) < 0.0001
            assert visit.accuracy_meters == 100.0  # 100000mm = 100m
            assert visit.source_type == SourceType.GOOGLE_TAKEOUT


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

        # Should get visits and locations from Google Takeout format
        visits = [e for e in entities if isinstance(e, LocationVisit)]
        locations = [e for e in entities if isinstance(e, Location)]

        assert len(visits) == 2  # From Timeline Edits
        assert len(locations) == 3  # From Labeled places
