"""Tests for pipeline DTOs and their validators."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from potluck.pipeline.dtos import (
    BatchStageResult,
    PipelineFilter,
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


class TestPipelineFilterPasses:
    """Tests for PipelineFilter.passes() method."""

    def test_none_timestamp_always_passes(self) -> None:
        """None occurred_at passes regardless of filter bounds."""
        f = PipelineFilter(
            since=datetime(2024, 1, 1, tzinfo=UTC),
            until=datetime(2024, 12, 31, tzinfo=UTC),
        )
        assert f.passes(None) is True

    def test_no_filters_always_passes(self) -> None:
        """With no since/until, any timestamp passes."""
        f = PipelineFilter()
        ts = datetime(2024, 6, 15, tzinfo=UTC)
        assert f.passes(ts) is True

    def test_before_since_fails(self) -> None:
        """Timestamp before since is rejected."""
        since = datetime(2024, 6, 1, tzinfo=UTC)
        f = PipelineFilter(since=since)
        early = datetime(2024, 5, 31, tzinfo=UTC)
        assert f.passes(early) is False

    def test_at_since_passes(self) -> None:
        """Timestamp exactly at since passes (inclusive lower bound)."""
        since = datetime(2024, 6, 1, tzinfo=UTC)
        f = PipelineFilter(since=since)
        assert f.passes(since) is True

    def test_after_since_passes(self) -> None:
        """Timestamp after since passes."""
        since = datetime(2024, 6, 1, tzinfo=UTC)
        f = PipelineFilter(since=since)
        later = datetime(2024, 7, 1, tzinfo=UTC)
        assert f.passes(later) is True

    def test_at_until_fails(self) -> None:
        """Timestamp exactly at until is rejected (exclusive upper bound)."""
        until = datetime(2024, 12, 31, tzinfo=UTC)
        f = PipelineFilter(until=until)
        assert f.passes(until) is False

    def test_before_until_passes(self) -> None:
        """Timestamp before until passes."""
        until = datetime(2024, 12, 31, tzinfo=UTC)
        f = PipelineFilter(until=until)
        early = datetime(2024, 12, 30, tzinfo=UTC)
        assert f.passes(early) is True

    def test_after_until_fails(self) -> None:
        """Timestamp after until is rejected."""
        until = datetime(2024, 12, 31, tzinfo=UTC)
        f = PipelineFilter(until=until)
        later = datetime(2025, 1, 1, tzinfo=UTC)
        assert f.passes(later) is False

    def test_within_range_passes(self) -> None:
        """Timestamp within [since, until) passes."""
        since = datetime(2024, 1, 1, tzinfo=UTC)
        until = datetime(2024, 12, 31, tzinfo=UTC)
        f = PipelineFilter(since=since, until=until)
        mid = datetime(2024, 6, 15, tzinfo=UTC)
        assert f.passes(mid) is True

    def test_outside_range_fails(self) -> None:
        """Timestamp outside [since, until) fails."""
        since = datetime(2024, 1, 1, tzinfo=UTC)
        until = datetime(2024, 12, 31, tzinfo=UTC)
        f = PipelineFilter(since=since, until=until)
        before = datetime(2023, 12, 31, tzinfo=UTC)
        after = datetime(2025, 1, 1, tzinfo=UTC)
        assert f.passes(before) is False
        assert f.passes(after) is False
