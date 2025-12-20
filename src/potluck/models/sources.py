"""Import source and run tracking models."""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from potluck.models.base import SourceType, _utc_now

if TYPE_CHECKING:
    pass


class ImportStatus(str, Enum):
    """Status of an import run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImportSource(SQLModel, table=True):
    """Registered data source for imports.

    Represents a configured data source that can be imported from,
    such as a Google Takeout archive path or Reddit account.
    """

    __tablename__ = "import_sources"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the import source",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the source was registered",
    )
    updated_at: datetime = Field(
        default_factory=_utc_now,
        sa_column_kwargs={"onupdate": _utc_now},
        description="When the source was last updated",
    )
    source_type: SourceType = Field(
        description="Type of data source (e.g., google_takeout, reddit)",
    )
    name: str = Field(
        description="Human-readable name for this source",
    )
    description: str | None = Field(
        default=None,
        description="Optional description of the source",
    )
    config: str | None = Field(
        default=None,
        description="JSON-encoded configuration for the source",
    )
    is_active: bool = Field(
        default=True,
        description="Whether this source is active for imports",
    )

    # Relationships
    import_runs: list["ImportRun"] = Relationship(back_populates="source")


class ImportRun(SQLModel, table=True):
    """Individual import operation.

    Tracks a single import run with statistics about what was processed.
    """

    __tablename__ = "import_runs"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the import run",
    )
    source_id: UUID = Field(
        foreign_key="import_sources.id",
        index=True,
        description="The source this run is importing from",
    )
    started_at: datetime = Field(
        default_factory=_utc_now,
        description="When the import run started",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="When the import run completed (if finished)",
    )
    status: ImportStatus = Field(
        default=ImportStatus.PENDING,
        description="Current status of the import run",
    )
    error_message: str | None = Field(
        default=None,
        description="Error message if the run failed",
    )

    # Statistics
    entities_found: int = Field(
        default=0,
        description="Total entities found in the source",
    )
    entities_created: int = Field(
        default=0,
        description="New entities created during this run",
    )
    entities_updated: int = Field(
        default=0,
        description="Existing entities updated during this run",
    )
    entities_skipped: int = Field(
        default=0,
        description="Entities skipped (duplicates, errors, etc.)",
    )
    entities_failed: int = Field(
        default=0,
        description="Entities that failed to import",
    )

    # Progress tracking
    progress_current: int = Field(
        default=0,
        description="Current progress counter",
    )
    progress_total: int | None = Field(
        default=None,
        description="Total items to process (if known)",
    )
    current_file: str | None = Field(
        default=None,
        description="Currently processing file/item",
    )

    # Relationships
    source: ImportSource = Relationship(back_populates="import_runs")

    @property
    def is_running(self) -> bool:
        """Check if this import is currently running."""
        return self.status == ImportStatus.RUNNING

    @property
    def is_finished(self) -> bool:
        """Check if this import has finished (successfully or not)."""
        return self.status in (
            ImportStatus.COMPLETED,
            ImportStatus.FAILED,
            ImportStatus.CANCELLED,
        )

    @property
    def progress_percent(self) -> float | None:
        """Calculate progress percentage if total is known."""
        if self.progress_total is None or self.progress_total == 0:
            return None
        return (self.progress_current / self.progress_total) * 100
