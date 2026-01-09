"""Unit tests for pipeline processing base classes and DTOs."""

from uuid import uuid4

from potluck.core.exceptions import ProcessingError
from potluck.pipeline.dtos import (
    BatchStageResult,
    StageResult,
    StageStatus,
)


class TestStageStatus:
    """Tests for StageStatus enum."""

    def test_all_statuses_defined(self) -> None:
        """All expected stage statuses should be defined."""
        assert StageStatus.PENDING.value == "pending"
        assert StageStatus.RUNNING.value == "running"
        assert StageStatus.COMPLETED.value == "completed"
        assert StageStatus.FAILED.value == "failed"
        assert StageStatus.SKIPPED.value == "skipped"


class TestStageResult:
    """Tests for StageResult DTO."""

    def test_result_creation(self) -> None:
        """StageResult should be created with required fields."""
        item_id = uuid4()
        result = StageResult(
            item_id=item_id,
            stage_name="test",
            status=StageStatus.COMPLETED,
        )

        assert result.item_id == item_id
        assert result.stage_name == "test"
        assert result.status == StageStatus.COMPLETED
        assert result.error_message is None
        assert result.processing_time_ms == 0
        assert result.data == {}

    def test_result_with_error(self) -> None:
        """StageResult should store error messages."""
        result = StageResult(
            item_id=uuid4(),
            stage_name="test",
            status=StageStatus.FAILED,
            error_message="Something went wrong",
        )

        assert result.status == StageStatus.FAILED
        assert result.error_message == "Something went wrong"

    def test_result_with_data(self) -> None:
        """StageResult should store extracted data."""
        result = StageResult(
            item_id=uuid4(),
            stage_name="hashing",
            status=StageStatus.COMPLETED,
            data={"file_hash": "abc123", "perceptual_hash": "def456"},
        )

        assert result.data["file_hash"] == "abc123"
        assert result.data["perceptual_hash"] == "def456"


class TestBatchStageResult:
    """Tests for BatchStageResult DTO."""

    def test_batch_result_creation(self) -> None:
        """BatchStageResult should aggregate individual results."""
        results = [
            StageResult(
                item_id=uuid4(),
                stage_name="test",
                status=StageStatus.COMPLETED,
            ),
            StageResult(
                item_id=uuid4(),
                stage_name="test",
                status=StageStatus.FAILED,
                error_message="Error",
            ),
            StageResult(
                item_id=uuid4(),
                stage_name="test",
                status=StageStatus.SKIPPED,
            ),
        ]

        batch = BatchStageResult(
            stage_name="test",
            total=3,
            completed=1,
            failed=1,
            skipped=1,
            results=results,
        )

        assert batch.total == 3
        assert batch.completed == 1
        assert batch.failed == 1
        assert batch.skipped == 1
        assert len(batch.results) == 3


class TestProcessingError:
    """Tests for ProcessingError exception."""

    def test_processing_error_creation(self) -> None:
        """ProcessingError should be created with message."""
        error = ProcessingError("Something went wrong")
        assert str(error) == "Something went wrong"
        assert error.message == "Something went wrong"

    def test_processing_error_inheritance(self) -> None:
        """ProcessingError should inherit from PipelineError."""
        from potluck.core.exceptions import PipelineError, PotluckError

        error = ProcessingError("Test error")
        assert isinstance(error, PipelineError)
        assert isinstance(error, PotluckError)
        assert isinstance(error, Exception)
