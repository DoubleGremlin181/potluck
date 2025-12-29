"""Import source and run tracking models."""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from potluck.models.base import SourceType
from potluck.models.utils import utc_now

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
    """Logical grouping for import operations.

    ImportSource represents a named collection of imports, typically corresponding
    to a data source type (e.g., "Google Takeout", "Reddit exports"). It serves as
    a logical grouping mechanism for organizing multiple ImportRun records.

    This is NOT tied to a specific file or account - it's a user-facing label.
    The same ImportSource can have multiple ImportRuns from different export files.

    Example:
        - ImportSource(name="Google Takeout", source_type=GOOGLE_TAKEOUT)
          - ImportRun(file_hash="abc123...")  # Takeout from Jan 2024
          - ImportRun(file_hash="def456...")  # Takeout from Jun 2024
    """

    __tablename__ = "import_sources"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the import source",
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        description="When the source was registered",
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column_kwargs={"onupdate": utc_now},
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
    """Single import execution from a specific file or directory.

    ImportRun tracks one import operation, including:
    - The source file's hash (file_hash) for detecting re-imports of the same file
    - Progress tracking for UI updates
    - Statistics about entities created/updated/skipped

    Deduplication:
    - file_hash: SHA256 of the import file. If a file with the same hash was
      already imported successfully, the pipeline skips re-processing.
    - Individual entities are deduplicated via BaseEntity.content_hash.

    Relationship to ImportSource:
    - Many ImportRuns can belong to one ImportSource
    - ImportSource is the logical grouping, ImportRun is the actual execution
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
        default_factory=utc_now,
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
    file_hash: str | None = Field(
        default=None,
        index=True,
        description="SHA256 hash of the source file for deduplication",
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
