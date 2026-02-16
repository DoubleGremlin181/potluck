"""MBOX email file ingestion stage.

Supports Thunderbird, Apple Mail, and any standard MBOX-format email files.
Uses RFC 2822 In-Reply-To and References headers for threading.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from potluck.core.logging import get_logger
from potluck.models.base import EntityType, IngestableEntity, SourceType
from potluck.pipeline.dtos import DetectionResult, PipelineFilter
from potluck.pipeline.ingestion.base import BaseIngestionStage
from potluck.pipeline.ingestion.mbox.emails import (
    count_emails_in_mbox,
    find_mbox_files,
    ingest_emails,
)
from potluck.pipeline.ingestion.registry import register

logger = get_logger(__name__)


@register
class MboxStage(BaseIngestionStage):
    """Ingestion stage for MBOX email files.

    Handles both individual .mbox files and directories containing multiple
    MBOX files (e.g., Thunderbird profile directories).
    """

    SOURCE_TYPE: ClassVar[SourceType] = SourceType.MBOX

    FILENAME_PATTERNS: ClassVar[list[str]] = [
        r".*\.mbox",  # Standard .mbox files
        r"Mail",  # Thunderbird Mail directory
        r"ImapMail",  # Thunderbird IMAP directory
        r"Inbox",  # Common mailbox name
        r"INBOX",  # Case variant
    ]

    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {
        EntityType.EMAIL,
    }

    def detect(self, path: Path) -> DetectionResult:
        """Scan for MBOX files and count emails."""
        mbox_paths, _ = find_mbox_files(path)
        if not mbox_paths:
            return DetectionResult()

        total_emails = sum(count_emails_in_mbox(p) for p in mbox_paths)

        entity_counts: dict[EntityType, int] = {}
        metadata: dict[str, str] = {}

        if total_emails > 0:
            entity_counts[EntityType.EMAIL] = total_emails
            metadata["source"] = "MBOX Email"
            metadata["mbox_files"] = str(len(mbox_paths))

        return DetectionResult(entity_counts=entity_counts, metadata=metadata)

    def execute(
        self,
        path: Path,
        entity_types: set[EntityType] | None = None,
        filters: PipelineFilter | None = None,
    ) -> Iterator[IngestableEntity]:
        """Yield Email and EmailThread entities from MBOX files."""
        types_to_process = (
            entity_types or self.SUPPORTED_ENTITY_TYPES
        ) & self.SUPPORTED_ENTITY_TYPES

        if EntityType.EMAIL not in types_to_process:
            return

        mbox_paths, folder_map = find_mbox_files(path)
        if not mbox_paths:
            logger.debug("No MBOX files found")
            return

        logger.info(f"Processing {len(mbox_paths)} MBOX file(s) at {path}")
        yield from ingest_emails(mbox_paths, folder_map, filters)
