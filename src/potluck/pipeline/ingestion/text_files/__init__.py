"""Text files and Obsidian vault ingestion stage.

Handles importing notes from:
- Plain .txt files
- Markdown .md files
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
from potluck.pipeline.ingestion.text_files.notes import (
    count_text_files,
    ingest_text_files,
    is_obsidian_vault,
)

logger = get_logger(__name__)


@register
class TextFilesStage(BaseIngestionStage):
    """Ingestion stage for text files and Obsidian vaults.

    Scans directories recursively for .txt and .md files.
    Detects Obsidian vaults by the presence of a .obsidian/ directory.
    """

    SOURCE_TYPE: ClassVar[SourceType] = SourceType.TEXT_FILES

    FILENAME_PATTERNS: ClassVar[list[str]] = [
        r"notes",
        r"documents",
        r"vault",
        r".*\.txt",
        r".*\.md",
    ]

    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {
        EntityType.KNOWLEDGE_NOTE,
    }

    def detect(self, path: Path) -> DetectionResult:
        """Scan for text files and return counts."""
        entity_counts: dict[EntityType, int] = {}
        metadata: dict[str, str] = {}

        count = count_text_files(path)
        if count > 0:
            entity_counts[EntityType.KNOWLEDGE_NOTE] = count
            metadata["source"] = "Obsidian Vault" if is_obsidian_vault(path) else "Text Files"

        return DetectionResult(entity_counts=entity_counts, metadata=metadata)

    def execute(
        self,
        path: Path,
        entity_types: set[EntityType] | None = None,
        filters: PipelineFilter | None = None,
    ) -> Iterator[IngestableEntity]:
        """Yield KnowledgeNote entities from text files."""
        types_to_process = (
            entity_types or self.SUPPORTED_ENTITY_TYPES
        ) & self.SUPPORTED_ENTITY_TYPES

        if EntityType.KNOWLEDGE_NOTE not in types_to_process:
            return

        yield from ingest_text_files(path)
