"""Unit tests for OCRProcessor."""

import pytest

# Skip entire module if ML dependencies not installed
pytest.importorskip("easyocr")

from uuid import uuid4

from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import StageStatus
from potluck.pipeline.processing.ocr import OCRProcessor


class TestOCRProcessor:
    """Tests for OCRProcessor."""

    def test_stage_has_name(self) -> None:
        """OCRProcessor should have a NAME attribute."""
        stage = OCRProcessor()
        assert stage.NAME == "ocr"

    def test_should_execute_only_images(self) -> None:
        """OCRProcessor should only process images."""
        stage = OCRProcessor()

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

        assert stage.should_execute(image_media) is True
        assert stage.should_execute(video_media) is False

    def test_skip_non_image(self) -> None:
        """OCRProcessor should skip non-image media."""
        stage = OCRProcessor()
        media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )

        result = stage.execute(media)

        assert result.status == StageStatus.SKIPPED

    def test_missing_file_fails(self) -> None:
        """OCRProcessor should fail for missing files."""
        stage = OCRProcessor()
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

    def test_default_languages(self) -> None:
        """OCRProcessor should default to English."""
        stage = OCRProcessor()
        assert stage._languages == ["en"]

    def test_custom_languages(self) -> None:
        """OCRProcessor should accept custom languages."""
        stage = OCRProcessor(languages=["en", "es", "fr"])
        assert stage._languages == ["en", "es", "fr"]

    def test_device_default(self) -> None:
        """OCRProcessor should auto-select device based on GPU env var."""
        import torch

        stage = OCRProcessor()
        # Device is now accessed through MLModels and respects GPU env var
        assert stage._models.device in [torch.device("cpu"), torch.device("cuda")]

    def test_device_can_be_explicit(self) -> None:
        """OCRProcessor should allow explicit device selection."""
        import torch

        stage = OCRProcessor(device="cpu")
        assert stage._models.device == torch.device("cpu")
