"""Unit tests for processing base classes and DTOs."""

from uuid import uuid4

from potluck.core.exceptions import ProcessingError
from potluck.processing.base import (
    BatchProcessingResult,
    ProcessingResult,
    ProcessingStatus,
)


class TestProcessingStatus:
    """Tests for ProcessingStatus enum."""

    def test_all_statuses_defined(self) -> None:
        """All expected processing statuses should be defined."""
        assert ProcessingStatus.PENDING.value == "pending"
        assert ProcessingStatus.RUNNING.value == "running"
        assert ProcessingStatus.COMPLETED.value == "completed"
        assert ProcessingStatus.FAILED.value == "failed"
        assert ProcessingStatus.SKIPPED.value == "skipped"


class TestProcessingResult:
    """Tests for ProcessingResult DTO."""

    def test_result_creation(self) -> None:
        """ProcessingResult should be created with required fields."""
        media_id = uuid4()
        result = ProcessingResult(
            media_id=media_id,
            processor_name="test",
            status=ProcessingStatus.COMPLETED,
        )

        assert result.media_id == media_id
        assert result.processor_name == "test"
        assert result.status == ProcessingStatus.COMPLETED
        assert result.error_message is None
        assert result.processing_time_ms == 0
        assert result.data == {}

    def test_result_with_error(self) -> None:
        """ProcessingResult should store error messages."""
        result = ProcessingResult(
            media_id=uuid4(),
            processor_name="test",
            status=ProcessingStatus.FAILED,
            error_message="Something went wrong",
        )

        assert result.status == ProcessingStatus.FAILED
        assert result.error_message == "Something went wrong"

    def test_result_with_data(self) -> None:
        """ProcessingResult should store extracted data."""
        result = ProcessingResult(
            media_id=uuid4(),
            processor_name="hashing",
            status=ProcessingStatus.COMPLETED,
            data={"file_hash": "abc123", "perceptual_hash": "def456"},
        )

        assert result.data["file_hash"] == "abc123"
        assert result.data["perceptual_hash"] == "def456"


class TestBatchProcessingResult:
    """Tests for BatchProcessingResult DTO."""

    def test_batch_result_creation(self) -> None:
        """BatchProcessingResult should aggregate individual results."""
        results = [
            ProcessingResult(
                media_id=uuid4(),
                processor_name="test",
                status=ProcessingStatus.COMPLETED,
            ),
            ProcessingResult(
                media_id=uuid4(),
                processor_name="test",
                status=ProcessingStatus.FAILED,
                error_message="Error",
            ),
            ProcessingResult(
                media_id=uuid4(),
                processor_name="test",
                status=ProcessingStatus.SKIPPED,
            ),
        ]

        batch = BatchProcessingResult(
            processor_name="test",
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
        """ProcessingError should inherit from PotluckError."""
        from potluck.core.exceptions import PotluckError

        error = ProcessingError("Test error")
        assert isinstance(error, PotluckError)
        assert isinstance(error, Exception)
