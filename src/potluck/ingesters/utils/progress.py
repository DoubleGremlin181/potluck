"""Progress tracking utilities for data ingestion."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from potluck.core.logging import get_logger
from potluck.models.utils import utc_now

if TYPE_CHECKING:
    from sqlmodel import Session

    from potluck.models.sources import ImportRun

logger = get_logger(__name__)


# Default interval for flushing progress to database (every N updates)
DEFAULT_FLUSH_INTERVAL = 100


@dataclass
class IngestionStats:
    """Statistics for an ingestion run.

    Tracks counts of entities created, updated, skipped, and failed.
    """

    created: int = 0
    """Entities successfully created."""

    updated: int = 0
    """Existing entities updated."""

    skipped: int = 0
    """Entities skipped (duplicates, filtered out, etc.)."""

    failed: int = 0
    """Entities that failed to process."""

    @property
    def total_processed(self) -> int:
        """Total entities processed (created + updated + skipped + failed)."""
        return self.created + self.updated + self.skipped + self.failed


class ProgressCallback(Protocol):
    """Protocol for external progress notifications.

    Implementations can send progress updates to external systems
    like Celery task state, websockets, or other notification channels.
    """

    def on_progress(self, current: int, total: int, percent: float) -> None:
        """Called when progress is updated.

        Args:
            current: Current progress count.
            total: Total expected count (0 if unknown).
            percent: Progress percentage (0-100, or 0 if total is unknown).
        """
        ...

    def on_file_change(self, filename: str) -> None:
        """Called when the current file being processed changes.

        Args:
            filename: Name of the file now being processed.
        """
        ...

    def on_stats_update(self, stats: IngestionStats) -> None:
        """Called when ingestion statistics are updated.

        Args:
            stats: Current ingestion statistics.
        """
        ...


class NoOpProgressCallback:
    """No-op implementation of ProgressCallback.

    Used when no external progress notification is needed.
    """

    def on_progress(self, current: int, total: int, percent: float) -> None:
        """No-op progress update."""
        pass

    def on_file_change(self, filename: str) -> None:
        """No-op file change notification."""
        pass

    def on_stats_update(self, stats: IngestionStats) -> None:
        """No-op stats update notification."""
        pass


@dataclass
class ProgressTracker:
    """Tracks and persists ingestion progress.

    Updates the ImportRun record in the database and optionally
    sends notifications via an external callback.

    Progress updates are batched to reduce database writes.
    """

    import_run: "ImportRun"
    """The ImportRun record to update."""

    session: "Session"
    """Database session for persisting updates."""

    callback: ProgressCallback = field(default_factory=NoOpProgressCallback)
    """Optional callback for external notifications."""

    flush_interval: int = DEFAULT_FLUSH_INTERVAL
    """Number of updates between database flushes."""

    _current: int = field(default=0, init=False)
    _total: int = field(default=0, init=False)
    _current_file: str | None = field(default=None, init=False)
    _stats: IngestionStats = field(default_factory=IngestionStats, init=False)
    _updates_since_flush: int = field(default=0, init=False)
    _last_flush: datetime = field(default_factory=utc_now, init=False)

    def set_total(self, total: int) -> None:
        """Set the expected total entity count.

        Args:
            total: Expected total number of entities to process.
        """
        self._total = total
        self.import_run.progress_total = total
        self._notify_progress()
        self._maybe_flush()

    def increment(self, count: int = 1) -> None:
        """Increment the progress counter.

        Args:
            count: Amount to increment by.
        """
        self._current += count
        self.import_run.progress_current = self._current
        self._updates_since_flush += 1
        self._notify_progress()
        self._maybe_flush()

    def set_current_file(self, filename: str) -> None:
        """Update the current file being processed.

        Args:
            filename: Name of the file now being processed.
        """
        self._current_file = filename
        self.import_run.current_file = filename
        self.callback.on_file_change(filename)
        self._maybe_flush()

    def update_stats(
        self,
        created: int = 0,
        updated: int = 0,
        skipped: int = 0,
        failed: int = 0,
    ) -> None:
        """Update ingestion statistics.

        Args:
            created: Additional entities created.
            updated: Additional entities updated.
            skipped: Additional entities skipped.
            failed: Additional entities failed.
        """
        self._stats.created += created
        self._stats.updated += updated
        self._stats.skipped += skipped
        self._stats.failed += failed

        # Sync to import run
        self.import_run.entities_created = self._stats.created
        self.import_run.entities_updated = self._stats.updated
        self.import_run.entities_skipped = self._stats.skipped
        self.import_run.entities_failed = self._stats.failed

        self._updates_since_flush += 1
        self.callback.on_stats_update(self._stats)
        self._maybe_flush()

    def get_stats(self) -> IngestionStats:
        """Get current ingestion statistics.

        Returns:
            Current IngestionStats instance.
        """
        return self._stats

    def flush(self) -> None:
        """Flush pending updates to the database."""
        self.session.add(self.import_run)
        self.session.commit()
        self._updates_since_flush = 0
        self._last_flush = utc_now()
        logger.debug(
            f"Progress flush: {self._current}/{self._total}, "
            f"stats: created={self._stats.created}, updated={self._stats.updated}, "
            f"skipped={self._stats.skipped}, failed={self._stats.failed}"
        )

    def _notify_progress(self) -> None:
        """Send progress notification to callback."""
        percent = 0.0
        if self._total > 0:
            percent = (self._current / self._total) * 100
        self.callback.on_progress(self._current, self._total, percent)

    def _maybe_flush(self) -> None:
        """Flush to database if enough updates have accumulated."""
        if self._updates_since_flush >= self.flush_interval:
            self.flush()

    @property
    def current(self) -> int:
        """Get current progress count."""
        return self._current

    @property
    def total(self) -> int:
        """Get expected total count."""
        return self._total

    @property
    def percent(self) -> float:
        """Get progress percentage (0-100)."""
        if self._total == 0:
            return 0.0
        return (self._current / self._total) * 100

    @property
    def current_file(self) -> str | None:
        """Get the name of the file currently being processed."""
        return self._current_file


class LoggingProgressCallback:
    """Progress callback that logs updates.

    Useful for CLI progress display or debugging.
    """

    def __init__(self, log_interval: int = 100):
        """Initialize the logging callback.

        Args:
            log_interval: Only log every N progress updates.
        """
        self.log_interval = log_interval
        self._update_count = 0

    def on_progress(self, current: int, total: int, percent: float) -> None:
        """Log progress update."""
        self._update_count += 1
        if self._update_count % self.log_interval == 0:
            if total > 0:
                logger.info(f"Progress: {current}/{total} ({percent:.1f}%)")
            else:
                logger.info(f"Progress: {current} entities processed")

    def on_file_change(self, filename: str) -> None:
        """Log file change."""
        logger.info(f"Processing: {filename}")

    def on_stats_update(self, stats: IngestionStats) -> None:
        """Log stats update."""
        pass  # Stats are logged on flush by ProgressTracker
