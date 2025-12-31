"""Base classes and DTOs for media processing.

This module provides the foundation for all media processors including:
- ProcessingStatus enum for tracking processor states
- ProcessingResult DTO for individual processing results
- BatchProcessingResult DTO for batch operations
- BaseProcessor abstract base class for all processors
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from potluck.models.media import Media


class ProcessingStatus(str, Enum):
    """Status of a processing operation."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProcessingResult(BaseModel):
    """Result of processing a single media item."""

    media_id: UUID = Field(description="ID of the processed media item")
    processor_name: str = Field(description="Name of the processor that ran")
    status: ProcessingStatus = Field(description="Processing outcome status")
    error_message: str | None = Field(
        default=None, description="Error message if processing failed"
    )
    processing_time_ms: int = Field(default=0, description="Time taken to process in milliseconds")
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted data from processing (e.g., file_hash, ocr_text)",
    )


class BatchProcessingResult(BaseModel):
    """Result of batch processing multiple media items."""

    processor_name: str = Field(description="Name of the processor that ran")
    total: int = Field(description="Total number of items in batch")
    completed: int = Field(description="Number of successfully processed items")
    failed: int = Field(description="Number of failed items")
    skipped: int = Field(description="Number of skipped items")
    results: list[ProcessingResult] = Field(
        default_factory=list, description="Individual results for each item"
    )


class BaseProcessor(ABC):
    """Abstract base class for all media processors.

    Processors extract information from media files and return structured results.
    Each processor has a NAME that identifies it and implements process() for
    single item processing.

    Subclasses can override process_batch() for optimized batch processing.
    """

    NAME: str  # Must be set by subclasses

    @abstractmethod
    def process(self, media: Media) -> ProcessingResult:
        """Process a single media item.

        Args:
            media: The media item to process.

        Returns:
            ProcessingResult with extracted data or error information.
        """
        ...

    def process_batch(self, media_items: list[Media]) -> BatchProcessingResult:
        """Process a batch of media items.

        Default implementation calls process() for each item. Subclasses can
        override for optimized batch processing (e.g., EasyOCR batch mode).

        Args:
            media_items: List of media items to process.

        Returns:
            BatchProcessingResult with aggregated statistics and individual results.
        """
        results = [self.process(m) for m in media_items]

        return BatchProcessingResult(
            processor_name=self.NAME,
            total=len(media_items),
            completed=sum(1 for r in results if r.status == ProcessingStatus.COMPLETED),
            failed=sum(1 for r in results if r.status == ProcessingStatus.FAILED),
            skipped=sum(1 for r in results if r.status == ProcessingStatus.SKIPPED),
            results=results,
        )

    def should_process(self, media: Media) -> bool:
        """Check if this media item should be processed.

        Default returns True. Subclasses can override to skip certain media types
        (e.g., OCR processor skipping non-image media).

        Args:
            media: The media item to check.

        Returns:
            True if the item should be processed, False to skip.
        """
        return True
