"""Tests for pipeline DTOs and their validators."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from potluck.pipeline.dtos import (
    BatchStageResult,
    PipelineStats,
    StageResult,
    StageStatus,
)


class TestStageResult:
    """Tests for StageResult DTO."""

    def test_valid_completed_result(self) -> None:
        """Completed result without error_message is valid."""
        result = StageResult(
            item_id=uuid4(),
            stage_name="test",
            status=StageStatus.COMPLETED,
        )
        assert result.status == StageStatus.COMPLETED
        assert result.error_message is None

    def test_valid_failed_result_with_error(self) -> None:
        """Failed result with error_message is valid."""
        result = StageResult(
            item_id=uuid4(),
            stage_name="test",
            status=StageStatus.FAILED,
            error_message="Something went wrong",
        )
        assert result.status == StageStatus.FAILED
        assert result.error_message == "Something went wrong"

    def test_valid_skipped_result_with_message(self) -> None:
        """Skipped result can have an error_message."""
        result = StageResult(
            item_id=uuid4(),
            stage_name="test",
            status=StageStatus.SKIPPED,
            error_message="Not applicable",
        )
        assert result.status == StageStatus.SKIPPED

    def test_failed_without_error_raises(self) -> None:
        """Failed status without error_message raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            StageResult(
                item_id=uuid4(),
                stage_name="test",
                status=StageStatus.FAILED,
            )
        assert "error_message is required" in str(exc_info.value)

    def test_error_message_on_completed_raises(self) -> None:
        """Error message on completed status raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            StageResult(
                item_id=uuid4(),
                stage_name="test",
                status=StageStatus.COMPLETED,
                error_message="Should not be here",
            )
        assert "error_message should only be set" in str(exc_info.value)

    def test_processing_time_must_be_non_negative(self) -> None:
        """Processing time cannot be negative."""
        with pytest.raises(ValidationError) as exc_info:
            StageResult(
                item_id=uuid4(),
                stage_name="test",
                status=StageStatus.COMPLETED,
                processing_time_ms=-1,
            )
        assert "greater than or equal to 0" in str(exc_info.value)


class TestBatchStageResult:
    """Tests for BatchStageResult DTO."""

    def test_counts_must_be_non_negative(self) -> None:
        """All counts must be non-negative."""
        with pytest.raises(ValidationError):
            BatchStageResult(
                stage_name="test",
                total=-1,
                completed=0,
                failed=0,
                skipped=0,
            )

    def test_valid_batch_result(self) -> None:
        """Valid batch result with non-negative counts."""
        result = BatchStageResult(
            stage_name="test",
            total=10,
            completed=8,
            failed=1,
            skipped=1,
        )
        assert result.total == 10


class TestPipelineStats:
    """Tests for PipelineStats DTO."""

    def test_entities_must_be_non_negative(self) -> None:
        """All entity counts must be non-negative."""
        with pytest.raises(ValidationError):
            PipelineStats(entities_created=-1)

    def test_valid_stats(self) -> None:
        """Valid stats with non-negative counts."""
        stats = PipelineStats(
            entities_created=10,
            entities_updated=5,
            entities_skipped=2,
            entities_failed=1,
        )
        assert stats.total_processed == 18
