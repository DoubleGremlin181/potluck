"""Data transfer objects for the pipeline module.

This module consolidates all DTOs used across ingestion and processing stages,
providing a unified set of data models for pipeline operations.
"""

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from potluck.models.base import EntityType, SourceType
from potluck.models.sources import ImportRun, ImportStatus


class StageStatus(str, Enum):
    """Status of a stage execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineFilter(BaseModel):
    """Filter for pipeline operations.

    Allows filtering entities by date range during ingestion.
    Stages use these filters to skip entities outside the specified range.
    """

    since: datetime | None = Field(
        default=None,
        description="Only process entities occurring on or after this datetime",
    )
    until: datetime | None = Field(
        default=None,
        description="Only process entities occurring before this datetime",
    )

    @model_validator(mode="after")
    def validate_and_normalize(self) -> Self:
        """Normalize naive datetimes to UTC-aware and validate range.

        CLI parsers (e.g. Typer) produce naive datetimes from date strings.
        We add UTC timezone for safe comparison with both aware and naive
        timestamps (passes() handles the normalization).
        """
        if self.since and self.since.tzinfo is None:
            self.since = self.since.replace(tzinfo=UTC)
        if self.until and self.until.tzinfo is None:
            self.until = self.until.replace(tzinfo=UTC)
        if self.since and self.until and self.since > self.until:
            raise ValueError("'since' must be before 'until'")
        return self

    def passes(self, occurred_at: datetime | None) -> bool:
        """Check if a timestamp falls within this filter's date range.

        Returns True when there is no timestamp or no active filters.
        Handles mixed naive/aware datetimes by normalizing naive to UTC.
        """
        if not occurred_at:
            return True
        # Normalize naive occurred_at to UTC for safe comparison
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        if self.since and occurred_at < self.since:
            return False
        return not (self.until and occurred_at >= self.until)


class StageResult(BaseModel):
    """Result of executing a single stage on one item."""

    item_id: UUID = Field(description="ID of the processed item")
    stage_name: str = Field(description="Name of the stage that ran")
    status: StageStatus = Field(description="Execution outcome status")
    error_message: str | None = Field(default=None, description="Error message if execution failed")
    processing_time_ms: int = Field(
        default=0, ge=0, description="Time taken to process in milliseconds"
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted data from processing (e.g., file_hash, ocr_text)",
    )

    @model_validator(mode="after")
    def validate_error_status_consistency(self) -> Self:
        """Ensure error_message is only set when status is FAILED."""
        if self.status == StageStatus.FAILED and not self.error_message:
            raise ValueError("error_message is required when status is FAILED")
        if self.error_message and self.status not in (StageStatus.FAILED, StageStatus.SKIPPED):
            raise ValueError("error_message should only be set for FAILED or SKIPPED status")
        return self


class BatchStageResult(BaseModel):
    """Result of batch stage execution."""

    stage_name: str = Field(description="Name of the stage that ran")
    total: int = Field(ge=0, description="Total number of items in batch")
    completed: int = Field(ge=0, description="Number of successfully processed items")
    failed: int = Field(ge=0, description="Number of failed items")
    skipped: int = Field(ge=0, description="Number of skipped items")
    results: list[StageResult] = Field(
        default_factory=list, description="Individual results for each item"
    )

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        """Ensure completed + failed + skipped does not exceed total."""
        if self.completed + self.failed + self.skipped > self.total:
            raise ValueError(
                f"completed ({self.completed}) + failed ({self.failed}) + "
                f"skipped ({self.skipped}) exceeds total ({self.total})"
            )
        return self


class DetectionResult(BaseModel):
    """Result of detecting available entity types in a source."""

    entity_counts: dict[EntityType, int] = Field(default_factory=dict)
    """Mapping of entity types to their counts."""

    metadata: dict[str, str] = Field(default_factory=dict)
    """Additional metadata about the detected content."""

    def total_entities(self) -> int:
        """Get total count of all entities."""
        return sum(self.entity_counts.values())

    model_config = {"arbitrary_types_allowed": True}


class DiscoveryResult(BaseModel):
    """Result of discovering source type and available entities."""

    source_path: Path
    """Original path that was discovered."""

    stage: type | None = None
    """Matched ingestion stage class (a type[BaseIngestionStage]), or None.

    Typed as ``type | None`` rather than ``type[BaseIngestionStage] | None``
    because importing BaseIngestionStage here would create a circular import
    (dtos → ingestion/base → dtos).

    When accessing stage attributes like SOURCE_TYPE, always check
    is_generic first or use the source_type property which handles None safely.
    """

    available_entities: dict[EntityType, int] = Field(default_factory=dict)
    """Entity types available and their counts."""

    metadata: dict[str, str] = Field(default_factory=dict)
    """Additional metadata from detection."""

    model_config = {"arbitrary_types_allowed": True}

    @property
    def is_generic(self) -> bool:
        """True if no specific ingestion stage matched."""
        return self.stage is None

    @property
    def has_content(self) -> bool:
        """True if any entities were found."""
        return bool(self.available_entities)

    @property
    def source_type(self) -> SourceType:
        """Get source type from stage or default to GENERIC."""
        if self.stage is not None:
            source: SourceType = self.stage.SOURCE_TYPE  # type: ignore[attr-defined]
            return source
        return SourceType.GENERIC


class PipelineStats(BaseModel):
    """Statistics from a pipeline run."""

    entities_created: int = Field(default=0, ge=0)
    entities_updated: int = Field(default=0, ge=0)
    entities_skipped: int = Field(default=0, ge=0)
    entities_failed: int = Field(default=0, ge=0)

    @property
    def total_processed(self) -> int:
        """Total entities processed."""
        return (
            self.entities_created
            + self.entities_updated
            + self.entities_skipped
            + self.entities_failed
        )


class PipelineResult(BaseModel):
    """Result of a complete pipeline run."""

    import_run: ImportRun
    """The ImportRun record with statistics."""

    stats: PipelineStats
    """Detailed pipeline statistics."""

    model_config = {"arbitrary_types_allowed": True}

    @property
    def success(self) -> bool:
        """Check if the pipeline completed successfully."""
        return self.import_run.status == ImportStatus.COMPLETED


# Rebuild PipelineResult to resolve forward reference to ImportRun
PipelineResult.model_rebuild()
