"""Base classes and DTOs for entity processing.

This module provides the foundation for all processing stages including:
- StageStatus enum for tracking stage states
- StageResult DTO for individual processing results
- BatchStageResult DTO for batch operations
- BaseProcessingStage abstract base class for all stages
"""

from abc import abstractmethod
from typing import ClassVar

from potluck.models.media import Media
from potluck.pipeline.base import Stage
from potluck.pipeline.dtos import BatchStageResult, StageResult, StageStatus


class BaseProcessingStage(Stage[Media, StageResult]):
    """Abstract base class for all processing stages.

    Processing stages extract information from media entities and return
    structured results. Each stage has a NAME that identifies it and
    implements execute() for single item processing.

    Subclasses can override execute_batch() for optimized batch processing.
    """

    NAME: ClassVar[str]  # Must be set by subclasses

    @abstractmethod
    def execute(self, media: Media) -> StageResult:
        """Process a single media item.

        Args:
            media: The media item to process.

        Returns:
            StageResult with extracted data or error information.
        """
        ...

    def execute_batch(self, media_items: list[Media]) -> BatchStageResult:
        """Process a batch of media items.

        Default implementation calls execute() for each item. Subclasses can
        override for optimized batch processing (e.g., EasyOCR batch mode).

        Args:
            media_items: List of media items to process.

        Returns:
            BatchStageResult with aggregated statistics and individual results.
        """
        results = [self.execute(m) for m in media_items]

        return BatchStageResult(
            stage_name=self.NAME,
            total=len(media_items),
            completed=sum(1 for r in results if r.status == StageStatus.COMPLETED),
            failed=sum(1 for r in results if r.status == StageStatus.FAILED),
            skipped=sum(1 for r in results if r.status == StageStatus.SKIPPED),
            results=results,
        )

    def should_execute(self, media: Media) -> bool:
        """Check if this media item should be processed.

        Default returns True. Subclasses can override to skip certain media types
        (e.g., OCR stage skipping non-image media).

        Args:
            media: The media item to check.

        Returns:
            True if the item should be processed, False to skip.
        """
        return True
