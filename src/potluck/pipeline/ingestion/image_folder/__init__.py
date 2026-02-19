"""Image folder ingestion stage.

Recursively scans any directory for image files.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from potluck.core.logging import get_logger
from potluck.models.base import EntityType, IngestableEntity, SourceType
from potluck.pipeline.dtos import DetectionResult, PipelineFilter
from potluck.pipeline.ingestion.base import BaseIngestionStage
from potluck.pipeline.ingestion.image_folder.images import count_media_files, ingest_media
from potluck.pipeline.ingestion.registry import register

logger = get_logger(__name__)


@register
class ImageFolderStage(BaseIngestionStage):
    """Ingestion stage for image file folders.

    Scans any directory recursively for image files.
    """

    SOURCE_TYPE: ClassVar[SourceType] = SourceType.GENERIC

    FILENAME_PATTERNS: ClassVar[list[str]] = [
        r"photos",
        r"pictures",
        r"images",
        r"camera",
        r"DCIM",
        r"Camera Roll",
    ]

    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {
        EntityType.MEDIA,
    }

    def detect(self, path: Path) -> DetectionResult:
        """Count media files in the directory."""
        count = count_media_files(path)

        entity_counts: dict[EntityType, int] = {}
        metadata: dict[str, str] = {}

        if count > 0:
            entity_counts[EntityType.MEDIA] = count
            metadata["source"] = "Image Folder"

        return DetectionResult(entity_counts=entity_counts, metadata=metadata)

    def execute(
        self,
        path: Path,
        entity_types: set[EntityType] | None = None,
        filters: PipelineFilter | None = None,
    ) -> Iterator[IngestableEntity]:
        """Yield Media entities from the folder."""
        types_to_process = (
            entity_types or self.SUPPORTED_ENTITY_TYPES
        ) & self.SUPPORTED_ENTITY_TYPES

        if EntityType.MEDIA not in types_to_process:
            return

        yield from ingest_media(path, filters)
