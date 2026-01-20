"""Tests for Google Photos media ingestion."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from potluck.models.base import SourceType
from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.ingestion.google_takeout.photos import (
    _get_occurred_at,
    _load_metadata,
    ingest_media,
)

# Path to test fixtures
FIXTURES_PATH = Path(__file__).parent.parent.parent.parent.parent / "fixtures" / "google_takeout"


class TestPhotosIngestion:
    """Tests for Google Photos ingestion."""

    def test_ingest_media_from_fixtures(self) -> None:
        """Ingest media from fixture files."""
        entities = list(ingest_media(FIXTURES_PATH))

        # Should have 3 media files (2 images + 1 video)
        assert len(entities) == 3

    def test_media_with_metadata(self) -> None:
        """Media with metadata has correct properties."""
        entities = list(ingest_media(FIXTURES_PATH))

        # Find the image with metadata
        beach_img = next(
            (m for m in entities if "Beach Sunset" in (m.original_filename or "")),
            None,
        )
        assert beach_img is not None
        assert beach_img.media_type == MediaType.IMAGE
        assert beach_img.latitude == 25.7617
        assert beach_img.longitude == -80.1918
        assert beach_img.altitude == 5.0
        assert beach_img.album_name == "Vacation 2024"

    def test_media_timestamp(self) -> None:
        """Media has correct timestamp from metadata."""
        entities = list(ingest_media(FIXTURES_PATH))

        beach_img = next(
            (m for m in entities if "Beach Sunset" in (m.original_filename or "")),
            None,
        )
        assert beach_img is not None
        assert beach_img.occurred_at is not None
        assert beach_img.occurred_at.year == 2024
        assert beach_img.occurred_at.month == 1
        assert beach_img.occurred_at.day == 16

    def test_media_without_metadata(self) -> None:
        """Media without metadata still has basic properties."""
        entities = list(ingest_media(FIXTURES_PATH))

        # Find the image without metadata (IMG_002.png)
        no_meta = next(
            (m for m in entities if "IMG_002" in (m.original_filename or "")),
            None,
        )
        assert no_meta is not None
        assert no_meta.media_type == MediaType.IMAGE
        # Should use filename as original_filename
        assert no_meta.original_filename == "IMG_002.png"
        # Should have file path
        assert "IMG_002.png" in no_meta.file_path
        # Should still have occurred_at from file mtime
        assert no_meta.occurred_at is not None

    def test_video_media_type(self) -> None:
        """Video files have correct media type."""
        entities = list(ingest_media(FIXTURES_PATH))

        video = next(
            (m for m in entities if m.media_type == MediaType.VIDEO),
            None,
        )
        assert video is not None
        # original_filename comes from metadata title
        assert video.original_filename == "Beach Waves.mp4"
        assert video.mime_type == "video/mp4"

    def test_source_type(self) -> None:
        """All media have correct source type."""
        entities = list(ingest_media(FIXTURES_PATH))

        for media in entities:
            assert media.source_type == SourceType.GOOGLE_TAKEOUT

    def test_file_hash(self) -> None:
        """Media has file hash computed."""
        entities = list(ingest_media(FIXTURES_PATH))

        for media in entities:
            assert media.file_hash is not None
            # SHA256 hashes are 64 characters
            assert len(media.file_hash) == 64

    def test_content_hash_uniqueness(self) -> None:
        """Each media has a unique content hash."""
        entities = list(ingest_media(FIXTURES_PATH))

        hashes = [m.content_hash for m in entities if m.content_hash]
        assert len(hashes) == len(set(hashes))

    def test_date_filter_since(self) -> None:
        """Date filter 'since' excludes earlier media."""
        filters = PipelineFilter(since=datetime(2024, 1, 17, tzinfo=UTC))
        entities = list(ingest_media(FIXTURES_PATH, filters))

        # Should only include media from Jan 17 onwards
        # The image without metadata will have recent file mtime, so may be included
        jan_16_media = [m for m in entities if "IMG_001" in (m.source_id or "")]
        assert len(jan_16_media) == 0, "Jan 16 image should be excluded"

    def test_date_filter_until(self) -> None:
        """Date filter 'until' excludes later media."""
        filters = PipelineFilter(until=datetime(2024, 1, 17, tzinfo=UTC))
        entities = list(ingest_media(FIXTURES_PATH, filters))

        # Should exclude Jan 17 video
        jan_17_media = [m for m in entities if "VID_001" in (m.source_id or "")]
        assert len(jan_17_media) == 0, "Jan 17 video should be excluded"

    def test_empty_directory(self) -> None:
        """Empty directory yields no entities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entities = list(ingest_media(Path(tmpdir)))
            assert entities == []

    def test_geo_coordinates(self) -> None:
        """Media with geo data has correct coordinates."""
        entities = list(ingest_media(FIXTURES_PATH))

        beach_img = next(
            (m for m in entities if "Beach Sunset" in (m.original_filename or "")),
            None,
        )
        assert beach_img is not None
        assert beach_img.latitude == 25.7617
        assert beach_img.longitude == -80.1918
        assert beach_img.altitude == 5.0


class TestHelperFunctions:
    """Tests for photo parsing helper functions."""

    def test_load_metadata_json_suffix(self) -> None:
        """Load metadata from .json suffix file."""
        media_file = FIXTURES_PATH / "Google Photos" / "Vacation 2024" / "IMG_001.jpg"
        metadata = _load_metadata(media_file)

        assert metadata is not None
        assert metadata.get("title") == "Beach Sunset.jpg"
        assert metadata.get("description") == "Beautiful sunset at the beach"

    def test_load_metadata_not_found(self) -> None:
        """Returns None when no metadata file exists."""
        media_file = FIXTURES_PATH / "Google Photos" / "Vacation 2024" / "IMG_002.png"
        metadata = _load_metadata(media_file)

        assert metadata is None

    def test_get_occurred_at_from_metadata(self) -> None:
        """Get timestamp from photoTakenTime in metadata."""
        media_file = FIXTURES_PATH / "Google Photos" / "Vacation 2024" / "IMG_001.jpg"
        metadata = _load_metadata(media_file)
        occurred_at = _get_occurred_at(media_file, metadata)

        assert occurred_at is not None
        assert occurred_at.year == 2024
        assert occurred_at.month == 1
        assert occurred_at.day == 16

    def test_get_occurred_at_fallback_to_mtime(self) -> None:
        """Get timestamp from file mtime when no metadata."""
        media_file = FIXTURES_PATH / "Google Photos" / "Vacation 2024" / "IMG_002.png"
        occurred_at = _get_occurred_at(media_file, None)

        # Should return file mtime
        assert occurred_at is not None


class TestIntegrationWithStage:
    """Integration tests with GoogleTakeoutStage."""

    def test_stage_executes_media_ingestion(self) -> None:
        """Stage correctly routes to media ingestion."""
        from potluck.models.base import EntityType
        from potluck.pipeline.ingestion.google_takeout import GoogleTakeoutStage

        stage = GoogleTakeoutStage()

        # Execute for media only
        entities = list(
            stage.execute(
                FIXTURES_PATH,
                entity_types={EntityType.MEDIA},
            )
        )

        # Should get media entities
        media_list = [e for e in entities if isinstance(e, Media)]

        assert len(media_list) == 3
