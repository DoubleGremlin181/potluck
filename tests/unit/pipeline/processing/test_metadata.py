"""Unit tests for MetadataProcessor."""

import tempfile
from pathlib import Path
from uuid import uuid4

from PIL import Image

from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import StageStatus
from potluck.pipeline.processing.metadata import MetadataProcessor


class TestMetadataProcessor:
    """Tests for MetadataProcessor."""

    @staticmethod
    def _create_test_image() -> Path:
        """Create a temporary test image without EXIF."""
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            img = Image.new("RGB", (100, 100), color="blue")
            img.save(f, "JPEG")
            return Path(f.name)

    def test_stage_has_name(self) -> None:
        """MetadataProcessor should have a NAME attribute."""
        stage = MetadataProcessor()
        assert stage.NAME == "metadata"

    def test_should_execute_only_images(self) -> None:
        """MetadataProcessor should only process images."""
        stage = MetadataProcessor()

        image_media = Media(
            id=uuid4(),
            file_path="/test.jpg",
            media_type=MediaType.IMAGE,
            source_type="generic",
        )
        video_media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )
        audio_media = Media(
            id=uuid4(),
            file_path="/test.mp3",
            media_type=MediaType.AUDIO,
            source_type="generic",
        )

        assert stage.should_execute(image_media) is True
        assert stage.should_execute(video_media) is False
        assert stage.should_execute(audio_media) is False

    def test_skip_non_image(self) -> None:
        """MetadataProcessor should skip non-image media."""
        stage = MetadataProcessor()
        media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )

        result = stage.execute(media)

        assert result.status == StageStatus.SKIPPED

    def test_missing_file_fails(self) -> None:
        """MetadataProcessor should fail for missing files."""
        stage = MetadataProcessor()
        media = Media(
            id=uuid4(),
            file_path="/nonexistent/file.jpg",
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result = stage.execute(media)

        assert result.status == StageStatus.FAILED
        assert result.error_message is not None
        assert "not found" in result.error_message.lower()

    def test_image_without_exif(self) -> None:
        """MetadataProcessor should handle images without EXIF data."""
        sample_image = self._create_test_image()
        stage = MetadataProcessor()
        media = Media(
            id=uuid4(),
            file_path=str(sample_image),
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result = stage.execute(media)

        assert result.status == StageStatus.COMPLETED
        assert result.data["has_exif"] is False
