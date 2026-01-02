"""Unit tests for OCRProcessor."""

import pytest

# Skip entire module if ML dependencies not installed
pytest.importorskip("easyocr")

from uuid import uuid4

from potluck.models.media import Media, MediaType
from potluck.processing.base import ProcessingStatus
from potluck.processing.ocr import OCRProcessor


class TestOCRProcessor:
    """Tests for OCRProcessor."""

    def test_processor_has_name(self) -> None:
        """OCRProcessor should have a NAME attribute."""
        processor = OCRProcessor()
        assert processor.NAME == "ocr"

    def test_should_process_only_images(self) -> None:
        """OCRProcessor should only process images."""
        processor = OCRProcessor()

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

        assert processor.should_process(image_media) is True
        assert processor.should_process(video_media) is False

    def test_skip_non_image(self) -> None:
        """OCRProcessor should skip non-image media."""
        processor = OCRProcessor()
        media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )

        result = processor.process(media)

        assert result.status == ProcessingStatus.SKIPPED

    def test_missing_file_fails(self) -> None:
        """OCRProcessor should fail for missing files."""
        processor = OCRProcessor()
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

    def test_default_languages(self) -> None:
        """OCRProcessor should default to English."""
        processor = OCRProcessor()
        assert processor._languages == ["en"]

    def test_custom_languages(self) -> None:
        """OCRProcessor should accept custom languages."""
        processor = OCRProcessor(languages=["en", "es", "fr"])
        assert processor._languages == ["en", "es", "fr"]

    def test_gpu_default_enabled(self) -> None:
        """OCRProcessor should enable GPU by default."""
        processor = OCRProcessor()
        assert processor._gpu is True

    def test_gpu_can_be_disabled(self) -> None:
        """OCRProcessor should allow disabling GPU."""
        processor = OCRProcessor(gpu=False)
        assert processor._gpu is False
