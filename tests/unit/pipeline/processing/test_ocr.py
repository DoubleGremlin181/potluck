"""Unit tests for OCRStage."""

import pytest

# Skip entire module if ML dependencies not installed
pytest.importorskip("easyocr")

from uuid import uuid4

from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import StageStatus
from potluck.pipeline.processing.ocr import OCRStage


class TestOCRStage:
    """Tests for OCRStage."""

    def test_stage_has_name(self) -> None:
        """OCRStage should have a NAME attribute."""
        stage = OCRStage()
        assert stage.NAME == "ocr"

    def test_should_execute_only_images(self) -> None:
        """OCRStage should only process images."""
        stage = OCRStage()

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
        """OCRStage should skip non-image media."""
        stage = OCRStage()
        media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )

        result = stage.execute(media)

        assert result.status == StageStatus.SKIPPED

    def test_missing_file_fails(self) -> None:
        """OCRStage should fail for missing files."""
        stage = OCRStage()
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
        """OCRStage should default to English."""
        stage = OCRStage()
        assert stage._languages == ["en"]

    def test_custom_languages(self) -> None:
        """OCRStage should accept custom languages."""
        stage = OCRStage(languages=["en", "es", "fr"])
        assert stage._languages == ["en", "es", "fr"]

    def test_gpu_default_enabled(self) -> None:
        """OCRStage should enable GPU by default."""
        stage = OCRStage()
        assert stage._gpu is True

    def test_gpu_can_be_disabled(self) -> None:
        """OCRStage should allow disabling GPU."""
        stage = OCRStage(gpu=False)
        assert stage._gpu is False
