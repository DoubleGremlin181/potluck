"""Text files and Obsidian vault ingestion stage.

Handles importing documents from:
- Plain .txt files
- Markdown .md files
- HTML files
- Obsidian vaults (with .obsidian/ directory detection)
"""

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from potluck.core.logging import get_logger
from potluck.models.base import EntityType, IngestableEntity, SourceType
from potluck.pipeline.dtos import DetectionResult, PipelineFilter
from potluck.pipeline.ingestion.base import BaseIngestionStage
from potluck.pipeline.ingestion.registry import register
from potluck.pipeline.ingestion.text_files.documents import (
    count_document_files,
    ingest_documents,
    is_obsidian_vault,
)

logger = get_logger(__name__)


@register
class TextFilesStage(BaseIngestionStage):
    """Ingestion stage for text files and Obsidian vaults.

    Scans directories recursively for .txt, .md, and .html files.
    Detects Obsidian vaults by the presence of a .obsidian/ directory.
    """

    SOURCE_TYPE: ClassVar[SourceType] = SourceType.GENERIC

    FILENAME_PATTERNS: ClassVar[list[str]] = [
        r"notes",
        r"documents",
        r"vault",
        r".*\.txt",
        r".*\.md",
    ]

    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {
        EntityType.DOCUMENT,
    }

    def detect(self, path: Path) -> DetectionResult:
        """Scan for text files and return counts."""
        entity_counts: dict[EntityType, int] = {}
        metadata: dict[str, str] = {}

        count = count_document_files(path)
        if count > 0:
            entity_counts[EntityType.DOCUMENT] = count
            metadata["source"] = "Obsidian Vault" if is_obsidian_vault(path) else "Text Files"

        return DetectionResult(entity_counts=entity_counts, metadata=metadata)

    def execute(
        self,
        path: Path,
        entity_types: set[EntityType] | None = None,
        filters: PipelineFilter | None = None,
    ) -> Iterator[IngestableEntity]:
        """Yield Document entities from text files."""
        types_to_process = (
            entity_types or self.SUPPORTED_ENTITY_TYPES
        ) & self.SUPPORTED_ENTITY_TYPES

        if EntityType.DOCUMENT not in types_to_process:
            return

        # Document has no occurred_at, so date filtering is not applicable
        yield from ingest_documents(path)
