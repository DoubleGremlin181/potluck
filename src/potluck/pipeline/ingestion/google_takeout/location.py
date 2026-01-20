"""Location History ingestion from Google Takeout and Android Timeline.

Handles:
- Timeline.json: Android Timeline export (rich data)
- Takeout/Timeline/Timeline Edits.json: Google Takeout timeline (sparse)
- Takeout/Maps/My labeled places/Labeled places.json: Named locations
"""

from collections.abc import Iterator
from pathlib import Path

from potluck.models.base import BaseEntity
from potluck.pipeline.dtos import PipelineFilter

# Placeholder - implementation in Commit 5


def ingest_location_visits(
    path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[BaseEntity]:
    """Ingest location visits from Google Timeline data.

    Args:
        path: Path to the extracted takeout directory.
        filters: Optional date range filters.

    Yields:
        LocationVisit and Location entities.
    """
    # Implementation in Commit 5
    yield from []
