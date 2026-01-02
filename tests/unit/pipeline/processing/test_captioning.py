"""Unit tests for CaptioningStage."""

import pytest

# Skip entire module if ML dependencies not installed
pytest.importorskip("torch")

from uuid import uuid4

from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import StageStatus
from potluck.pipeline.processing.captioning import CaptioningStage


class TestCaptioningStage:
    """Tests for CaptioningStage."""

    def test_stage_has_name(self) -> None:
        """CaptioningStage should have a NAME attribute."""
        stage = CaptioningStage()
        assert stage.NAME == "captioning"

    def test_should_execute_only_images(self) -> None:
        """CaptioningStage should only process images."""
        stage = CaptioningStage()

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
        """CaptioningStage should skip non-image media."""
        stage = CaptioningStage()
        media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )

        result = stage.execute(media)

        assert result.status == StageStatus.SKIPPED

    def test_missing_file_fails(self) -> None:
        """CaptioningStage should fail for missing files."""
        stage = CaptioningStage()
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

    def test_default_model_settings(self) -> None:
        """CaptioningStage should use BLIP-2 model by default."""
        stage = CaptioningStage()
        assert stage._model_name == "Salesforce/blip2-opt-2.7b"
        assert stage._max_length == 50

    def test_custom_model_settings(self) -> None:
        """CaptioningStage should accept custom model settings."""
        stage = CaptioningStage(
            model_name="custom/model",
            max_length=100,
            device="cpu",
        )
        assert stage._model_name == "custom/model"
        assert stage._max_length == 100
        assert stage._device == "cpu"
