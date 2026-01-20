"""Google Photos media ingestion from Google Takeout.

Handles:
- Google Photos/*: Photo and video files
- *.supplemental-metadata.json: Sidecar metadata files
"""

from collections.abc import Iterator
from pathlib import Path

from potluck.models.base import BaseEntity
from potluck.pipeline.dtos import PipelineFilter

# Placeholder - implementation in Commit 6


def ingest_media(
    path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[BaseEntity]:
    """Ingest Google Photos media from Google Takeout.

    Args:
        path: Path to the extracted takeout directory.
        filters: Optional date range filters.

    Yields:
        Media entities.
    """
    # Implementation in Commit 6
    yield from []
