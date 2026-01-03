"""Data transfer objects for the pipeline module.

This module consolidates all DTOs used across ingestion and processing stages,
providing a unified set of data models for pipeline operations.
"""

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from potluck.models.base import EntityType

if TYPE_CHECKING:
    from potluck.models.sources import ImportRun


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
    def validate_date_range(self) -> Self:
        """Validate that since is before until when both are specified."""
        if self.since and self.until and self.since > self.until:
            raise ValueError("'since' must be before 'until'")
        return self


class StageResult(BaseModel):
    """Result of executing a single stage on one item."""

    item_id: UUID = Field(description="ID of the processed item")
    stage_name: str = Field(description="Name of the stage that ran")
    status: StageStatus = Field(description="Execution outcome status")
    error_message: str | None = Field(default=None, description="Error message if execution failed")
    processing_time_ms: int = Field(default=0, description="Time taken to process in milliseconds")
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted data from processing (e.g., file_hash, ocr_text)",
    )


class BatchStageResult(BaseModel):
    """Result of batch stage execution."""

    stage_name: str = Field(description="Name of the stage that ran")
    total: int = Field(description="Total number of items in batch")
    completed: int = Field(description="Number of successfully processed items")
    failed: int = Field(description="Number of failed items")
    skipped: int = Field(description="Number of skipped items")
    results: list[StageResult] = Field(
        default_factory=list, description="Individual results for each item"
    )


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

    stage: Any = None
    """Matched ingestion stage class, or None if no stage matched.

    The value should be a type[BaseIngestionStage] or None. Any is used
    to avoid forward reference issues with Pydantic.

    Note: When accessing stage attributes like SOURCE_TYPE, always check
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
    def source_type(self) -> Any:
        """Get source type from stage or default to GENERIC."""
        from potluck.models.base import SourceType

        if self.stage is not None:
            return self.stage.SOURCE_TYPE
        return SourceType.GENERIC


class PipelineStats(BaseModel):
    """Statistics from a pipeline run."""

    entities_created: int = 0
    entities_updated: int = 0
    entities_skipped: int = 0
    entities_failed: int = 0

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

    import_run: "ImportRun"
    """The ImportRun record with statistics."""

    stats: PipelineStats
    """Detailed pipeline statistics."""

    model_config = {"arbitrary_types_allowed": True}

    @property
    def success(self) -> bool:
        """Check if the pipeline completed successfully."""
        from potluck.models.sources import ImportStatus

        return self.import_run.status == ImportStatus.COMPLETED


# Rebuild PipelineResult to resolve forward reference to ImportRun
def _rebuild_models() -> None:
    """Rebuild models with forward references after all types are defined."""
    from potluck.models.sources import ImportRun  # noqa: F401

    PipelineResult.model_rebuild()


_rebuild_models()
