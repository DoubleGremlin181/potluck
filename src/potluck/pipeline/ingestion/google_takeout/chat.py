"""Google Chat/Hangouts message ingestion from Google Takeout.

Handles:
- Google Chat/Groups/*/messages.json: Chat messages
- Google Chat/Groups/*/group_info.json: Thread metadata
"""

from collections.abc import Iterator
from pathlib import Path

from potluck.models.base import BaseEntity
from potluck.pipeline.dtos import PipelineFilter

# Placeholder - implementation in Commit 3


def ingest_chat_messages(
    path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[BaseEntity]:
    """Ingest Google Chat messages from Google Takeout.

    Args:
        path: Path to the extracted takeout directory.
        filters: Optional date range filters.

    Yields:
        ChatThread and ChatMessage entities.
    """
    # Implementation in Commit 3
    yield from []
