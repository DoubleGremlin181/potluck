"""Tests for Android Timeline ingestion stage."""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from potluck.models.base import EntityType, SourceType
from potluck.models.locations import LocationHistory, LocationVisit
from potluck.pipeline import detect_stage, list_stages, register
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.ingestion.android_timeline import AndroidTimelineStage
from potluck.pipeline.ingestion.android_timeline.timeline import (
    _parse_iso_timestamp,
    _parse_lat_lng,
    ingest_android_timeline,
)


@pytest.fixture(autouse=True)
def ensure_stage_registered() -> None:
    """Ensure AndroidTimelineStage is registered before each test."""
    register(AndroidTimelineStage)


class TestAndroidTimelineStageRegistration:
    """Tests for stage registration and auto-discovery."""

    def test_stage_is_registered(self) -> None:
        """AndroidTimelineStage is auto-registered when module is imported."""
        stages = list_stages()
        assert AndroidTimelineStage in stages

    def test_source_type(self) -> None:
        """Stage has correct source type."""
        assert AndroidTimelineStage.SOURCE_TYPE == SourceType.ANDROID_TIMELINE

    def test_supported_entity_types(self) -> None:
        """Stage supports expected entity types."""
        expected = {EntityType.LOCATION_VISIT}
        assert expected == AndroidTimelineStage.SUPPORTED_ENTITY_TYPES


class TestAndroidTimelineStageDetection:
    """Tests for filename pattern matching."""

    def test_detect_timeline_json(self) -> None:
        """Detects Timeline.json files."""
        result = detect_stage(Path("Timeline.json"))
        assert result is AndroidTimelineStage

    def test_no_match_other_json(self) -> None:
        """Does not match other JSON files."""
        result = detect_stage(Path("random.json"))
        assert result is None


class TestAndroidTimelineStageDetectionCounts:
    """Tests for entity count detection."""

    def test_detect_empty_directory(self) -> None:
        """Returns empty counts for empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stage = AndroidTimelineStage()
            result = stage.detect(Path(tmpdir))
            assert result.entity_counts == {}
            assert result.metadata == {}

    def test_detect_timeline_file(self) -> None:
        """Detects Timeline.json and estimates counts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Timeline.json with some content
            timeline_data = {
                "semanticSegments": [
                    {
                        "startTime": "2024-01-01T10:00:00Z",
                        "endTime": "2024-01-01T12:00:00Z",
                        "visit": {
                            "topCandidate": {"placeLocation": {"latLng": "40.7128°, -74.0060°"}}
                        },
                    }
                ]
            }
            timeline_file = Path(tmpdir) / "Timeline.json"
            timeline_file.write_text(json.dumps(timeline_data))

            stage = AndroidTimelineStage()
            result = stage.detect(Path(tmpdir))

            assert EntityType.LOCATION_VISIT in result.entity_counts
            assert result.entity_counts[EntityType.LOCATION_VISIT] > 0
            assert result.metadata.get("source") == "Android Timeline"


class TestDetectCountMatchesExecute:
    """Tests that detect() entity count matches what execute() yields."""

    def test_detect_count_includes_timeline_path_points(self) -> None:
        """detect() counts timelinePath points, not just segments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            timeline_data = {
                "semanticSegments": [
                    {
                        "startTime": "2024-01-15T10:00:00Z",
                        "endTime": "2024-01-15T12:00:00Z",
                        "visit": {
                            "topCandidate": {
                                "placeLocation": {"latLng": "40.7128°, -74.0060°"},
                            }
                        },
                    },
                    {
                        "startTime": "2024-01-15T12:00:00Z",
                        "timelinePath": [
                            {"point": "40.7128°, -74.0060°", "time": "2024-01-15T12:00:00Z"},
                            {"point": "40.7130°, -74.0062°", "time": "2024-01-15T12:01:00Z"},
                            {"point": "40.7132°, -74.0064°", "time": "2024-01-15T12:02:00Z"},
                        ],
                    },
                    {
                        "startTime": "2024-01-15T12:30:00Z",
                        "endTime": "2024-01-15T13:00:00Z",
                        "activity": {
                            "start": {"latLng": "40.7132°, -74.0064°"},
                            "topCandidate": {"type": "WALKING", "probability": 0.8},
                        },
                    },
                ]
            }
            timeline_file = Path(tmpdir) / "Timeline.json"
            timeline_file.write_text(json.dumps(timeline_data))

            stage = AndroidTimelineStage()
            detection = stage.detect(Path(tmpdir))
            execute_count = len(list(stage.execute(Path(tmpdir))))

            # detect count should match execute count: 1 visit + 3 path + 1 activity = 5
            assert detection.entity_counts[EntityType.LOCATION_VISIT] == 5
            assert detection.entity_counts[EntityType.LOCATION_VISIT] == execute_count


class TestAndroidTimelineIngestion:
    """Tests for timeline ingestion logic."""

    def test_ingest_visit_segment(self) -> None:
        """Ingests visit segments correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            timeline_data = {
                "semanticSegments": [
                    {
                        "startTime": "2024-01-15T10:00:00Z",
                        "endTime": "2024-01-15T12:00:00Z",
                        "visit": {
                            "topCandidate": {
                                "placeLocation": {"latLng": "40.7128°, -74.0060°"},
                                "placeId": "test_place_123",
                                "semanticType": "HOME",
                                "probability": 0.95,
                            }
                        },
                    }
                ]
            }
            timeline_file = Path(tmpdir) / "Timeline.json"
            timeline_file.write_text(json.dumps(timeline_data))

            entities = list(ingest_android_timeline(timeline_file))

            assert len(entities) == 1
            visit = entities[0]
            assert isinstance(visit, LocationVisit)
            assert visit.source_type == SourceType.ANDROID_TIMELINE
            assert visit.latitude == 40.7128
            assert visit.longitude == -74.0060
            assert visit.place_id == "test_place_123"
            assert visit.place_name == "Home"
            assert visit.confidence == 0.95

    def test_ingest_activity_segment(self) -> None:
        """Ingests activity segments correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            timeline_data = {
                "semanticSegments": [
                    {
                        "startTime": "2024-01-15T12:00:00Z",
                        "endTime": "2024-01-15T12:30:00Z",
                        "activity": {
                            "start": {"latLng": "40.7128°, -74.0060°"},
                            "topCandidate": {"type": "WALKING", "probability": 0.8},
                        },
                    }
                ]
            }
            timeline_file = Path(tmpdir) / "Timeline.json"
            timeline_file.write_text(json.dumps(timeline_data))

            entities = list(ingest_android_timeline(timeline_file))

            assert len(entities) == 1
            visit = entities[0]
            assert isinstance(visit, LocationVisit)
            assert visit.activity_type == "WALKING"

    def test_ingest_timeline_path(self) -> None:
        """Ingests timeline path points correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            timeline_data = {
                "semanticSegments": [
                    {
                        "startTime": "2024-01-15T12:00:00Z",
                        "timelinePath": [
                            {"point": "40.7128°, -74.0060°", "time": "2024-01-15T12:00:00Z"},
                            {"point": "40.7130°, -74.0062°", "time": "2024-01-15T12:01:00Z"},
                        ],
                    }
                ]
            }
            timeline_file = Path(tmpdir) / "Timeline.json"
            timeline_file.write_text(json.dumps(timeline_data))

            entities = list(ingest_android_timeline(timeline_file))

            assert len(entities) == 2
            for entity in entities:
                assert isinstance(entity, LocationHistory)
                assert entity.source_type == SourceType.ANDROID_TIMELINE

    def test_date_filter_since(self) -> None:
        """Date filter 'since' excludes earlier segments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            timeline_data = {
                "semanticSegments": [
                    {
                        "startTime": "2024-01-01T10:00:00Z",
                        "visit": {
                            "topCandidate": {"placeLocation": {"latLng": "40.7128°, -74.0060°"}}
                        },
                    },
                    {
                        "startTime": "2024-01-15T10:00:00Z",
                        "visit": {
                            "topCandidate": {"placeLocation": {"latLng": "40.7130°, -74.0062°"}}
                        },
                    },
                ]
            }
            timeline_file = Path(tmpdir) / "Timeline.json"
            timeline_file.write_text(json.dumps(timeline_data))

            filters = PipelineFilter(since=datetime(2024, 1, 10, tzinfo=UTC))
            entities = list(ingest_android_timeline(timeline_file, filters))

            assert len(entities) == 1

    def test_empty_file(self) -> None:
        """Empty Timeline.json yields no entities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            timeline_file = Path(tmpdir) / "Timeline.json"
            timeline_file.write_text('{"semanticSegments": []}')

            entities = list(ingest_android_timeline(timeline_file))
            assert entities == []


class TestHelperFunctions:
    """Tests for parsing helper functions."""

    def test_parse_lat_lng_degrees(self) -> None:
        """Parse coordinates with degree symbols."""
        lat, lng = _parse_lat_lng("40.7128°, -74.0060°")
        assert lat == 40.7128
        assert lng == -74.0060

    def test_parse_lat_lng_no_degrees(self) -> None:
        """Parse coordinates without degree symbols."""
        lat, lng = _parse_lat_lng("40.7128, -74.0060")
        assert lat == 40.7128
        assert lng == -74.0060

    def test_parse_lat_lng_empty(self) -> None:
        """Empty string returns None."""
        lat, lng = _parse_lat_lng("")
        assert lat is None
        assert lng is None

    def test_parse_iso_timestamp(self) -> None:
        """Parse ISO timestamp."""
        dt = _parse_iso_timestamp("2024-01-15T10:30:00Z")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_parse_iso_timestamp_with_offset(self) -> None:
        """Parse ISO timestamp with timezone offset."""
        dt = _parse_iso_timestamp("2024-01-15T10:30:00-05:00")
        assert dt is not None
        assert dt.tzinfo == UTC

    def test_parse_iso_timestamp_none(self) -> None:
        """None input returns None."""
        dt = _parse_iso_timestamp(None)
        assert dt is None


class TestIntegrationWithStage:
    """Integration tests with AndroidTimelineStage."""

    def test_stage_executes_ingestion(self) -> None:
        """Stage correctly routes to timeline ingestion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            timeline_data = {
                "semanticSegments": [
                    {
                        "startTime": "2024-01-15T10:00:00Z",
                        "visit": {
                            "topCandidate": {"placeLocation": {"latLng": "40.7128°, -74.0060°"}}
                        },
                    }
                ]
            }
            timeline_file = Path(tmpdir) / "Timeline.json"
            timeline_file.write_text(json.dumps(timeline_data))

            stage = AndroidTimelineStage()
            entities = list(stage.execute(Path(tmpdir), entity_types={EntityType.LOCATION_VISIT}))

            assert len(entities) == 1
            assert isinstance(entities[0], LocationVisit)
