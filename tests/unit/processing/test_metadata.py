"""Unit tests for MetadataProcessor."""

import tempfile
from pathlib import Path
from uuid import uuid4

from PIL import Image

from potluck.models.media import Media, MediaType
from potluck.processing.base import ProcessingStatus
from potluck.processing.metadata import MetadataProcessor


class TestMetadataProcessor:
    """Tests for MetadataProcessor."""

    @staticmethod
    def _create_test_image() -> Path:
        """Create a temporary test image without EXIF."""
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            img = Image.new("RGB", (100, 100), color="blue")
            img.save(f, "JPEG")
            return Path(f.name)

    def test_processor_has_name(self) -> None:
        """MetadataProcessor should have a NAME attribute."""
        processor = MetadataProcessor()
        assert processor.NAME == "metadata"

    def test_should_process_only_images(self) -> None:
        """MetadataProcessor should only process images."""
        processor = MetadataProcessor()

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

        assert processor.should_process(image_media) is True
        assert processor.should_process(video_media) is False
        assert processor.should_process(audio_media) is False

    def test_skip_non_image(self) -> None:
        """MetadataProcessor should skip non-image media."""
        processor = MetadataProcessor()
        media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )

        result = processor.process(media)

        assert result.status == ProcessingStatus.SKIPPED

    def test_missing_file_fails(self) -> None:
        """MetadataProcessor should fail for missing files."""
        processor = MetadataProcessor()
        media = Media(
            id=uuid4(),
            file_path="/nonexistent/file.jpg",
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result = processor.process(media)

        assert result.status == ProcessingStatus.FAILED
        assert result.error_message is not None
        assert "not found" in result.error_message.lower()

    def test_image_without_exif(self) -> None:
        """MetadataProcessor should handle images without EXIF data."""
        sample_image = self._create_test_image()
        processor = MetadataProcessor()
        media = Media(
            id=uuid4(),
            file_path=str(sample_image),
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result = processor.process(media)

        assert result.status == ProcessingStatus.COMPLETED
        assert result.data["has_exif"] is False
