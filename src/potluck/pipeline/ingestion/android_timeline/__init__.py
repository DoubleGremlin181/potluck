"""Android Timeline ingestion stage.

Handles importing location data from Android Timeline export (Timeline.json).
This is a richer format than Google Takeout's Timeline data, containing:
- Semantic segments (visit and activity data)
- Timeline path (raw GPS points)
- Place information with confidence scores
"""

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from potluck.core.logging import get_logger
from potluck.models.base import EntityType, IngestableEntity, SourceType
from potluck.pipeline.dtos import DetectionResult, PipelineFilter
from potluck.pipeline.ingestion.android_timeline.timeline import ingest_android_timeline
from potluck.pipeline.ingestion.base import BaseIngestionStage
from potluck.pipeline.ingestion.registry import register

logger = get_logger(__name__)

# Constants for detection estimation
BYTES_PER_SEGMENT = 500  # Approximate bytes per semantic segment


@register
class AndroidTimelineStage(BaseIngestionStage):
    """Ingestion stage for Android Timeline export (Timeline.json).

    Handles the Timeline.json file exported directly from Android's
    Settings > Location > Timeline > Export timeline feature.

    This provides richer data than Google Takeout's Timeline, including:
    - Visit segments with place information and duration
    - Activity segments (walking, driving, etc.)
    - Raw GPS path points
    """

    SOURCE_TYPE: ClassVar[SourceType] = SourceType.ANDROID_TIMELINE

    FILENAME_PATTERNS: ClassVar[list[str]] = [
        r"Timeline\.json",  # Android Timeline export
    ]

    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {
        EntityType.LOCATION_VISIT,
    }

    def detect(self, path: Path) -> DetectionResult:
        """Scan for Timeline.json and estimate entity counts.

        Args:
            path: Path to the directory containing Timeline.json.

        Returns:
            DetectionResult with entity type counts and metadata.
        """
        entity_counts: dict[EntityType, int] = {}
        metadata: dict[str, str] = {}

        timeline_file = path / "Timeline.json"
        # Also check if path itself is Timeline.json
        if not timeline_file.exists() and path.name == "Timeline.json" and path.is_file():
            timeline_file = path

        if timeline_file.exists() and timeline_file.is_file():
            # Estimate count from file size
            size = timeline_file.stat().st_size
            estimated_count = max(1, size // BYTES_PER_SEGMENT)
            entity_counts[EntityType.LOCATION_VISIT] = estimated_count

            metadata["source"] = "Android Timeline"
            metadata["file"] = timeline_file.name

        return DetectionResult(entity_counts=entity_counts, metadata=metadata)

    def execute(
        self,
        path: Path,
        entity_types: set[EntityType] | None = None,
        filters: PipelineFilter | None = None,
    ) -> Iterator[IngestableEntity]:
        """Yield location entities from Android Timeline export.

        Args:
            path: Path to the directory containing Timeline.json.
            entity_types: Set of entity types to ingest (None = all supported).
            filters: Optional date range filters.

        Yields:
            LocationVisit and LocationHistory entities.
        """
        # Default to all supported types if none specified
        types_to_process = entity_types or self.SUPPORTED_ENTITY_TYPES
        types_to_process = types_to_process & self.SUPPORTED_ENTITY_TYPES

        if EntityType.LOCATION_VISIT not in types_to_process:
            return

        # Find Timeline.json
        timeline_file = path / "Timeline.json"
        if not timeline_file.exists():
            if path.name == "Timeline.json" and path.is_file():
                timeline_file = path
            else:
                logger.debug("No Timeline.json found")
                return

        logger.info(f"Processing Android Timeline from {timeline_file}")
        yield from ingest_android_timeline(timeline_file, filters)
