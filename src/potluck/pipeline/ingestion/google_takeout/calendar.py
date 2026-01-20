"""Google Calendar event ingestion from Google Takeout.

Handles:
- Calendar/*.ics: iCalendar files with VEVENT entries
"""

from collections.abc import Iterator
from pathlib import Path

from potluck.models.base import BaseEntity
from potluck.pipeline.dtos import PipelineFilter

# Placeholder - implementation in Commit 4


def ingest_calendar_events(
    path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[BaseEntity]:
    """Ingest Google Calendar events from Google Takeout.

    Args:
        path: Path to the extracted takeout directory.
        filters: Optional date range filters.

    Yields:
        CalendarEvent entities.
    """
    # Implementation in Commit 4
    yield from []
