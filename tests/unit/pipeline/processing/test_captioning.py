"""Unit tests for CaptioningProcessor."""

import pytest

# Skip entire module if ML dependencies not installed
pytest.importorskip("torch")

from uuid import uuid4

from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import StageStatus
from potluck.pipeline.processing.processors.captioning import CaptioningProcessor


class TestCaptioningProcessor:
    """Tests for CaptioningProcessor."""

    def test_stage_has_name(self) -> None:
        """CaptioningProcessor should have a NAME attribute."""
        stage = CaptioningProcessor()
        assert stage.NAME == "captioning"

    def test_should_execute_only_images(self) -> None:
        """CaptioningProcessor should only process images."""
        stage = CaptioningProcessor()

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
        """CaptioningProcessor should skip non-image media."""
        stage = CaptioningProcessor()
        media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )

        result = stage.execute(media)

        assert result.status == StageStatus.SKIPPED

    def test_missing_file_fails(self) -> None:
        """CaptioningProcessor should fail for missing files."""
        stage = CaptioningProcessor()
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
        """CaptioningProcessor should use Florence-2 model by default."""
        stage = CaptioningProcessor()
        assert stage._model_name == "microsoft/Florence-2-base-ft"

    def test_custom_model_settings(self) -> None:
        """CaptioningProcessor should accept custom model settings."""
        stage = CaptioningProcessor(
            model_name="custom/model",
            device="cpu",
        )
        assert stage._model_name == "custom/model"
        # Device is now accessed through MLModels
        import torch

        assert stage._models.device == torch.device("cpu")

    def test_task_prompt_constant(self) -> None:
        """CaptioningProcessor should use DETAILED_CAPTION task prompt."""
        assert CaptioningProcessor.TASK_PROMPT == "<DETAILED_CAPTION>"
