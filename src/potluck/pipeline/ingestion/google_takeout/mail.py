"""Gmail email ingestion from Google Takeout.

Handles:
- Mail/*.mbox: Email messages in MBOX format
"""

from collections.abc import Iterator
from pathlib import Path

from potluck.models.base import BaseEntity
from potluck.pipeline.dtos import PipelineFilter

# Placeholder - implementation in Commit 7


def ingest_emails(
    path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[BaseEntity]:
    """Ingest Gmail emails from Google Takeout.

    Args:
        path: Path to the extracted takeout directory.
        filters: Optional date range filters.

    Yields:
        EmailThread and Email entities.
    """
    # Implementation in Commit 7
    yield from []
