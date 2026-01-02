"""Data source ingesters for Potluck.

This module provides the ingestion infrastructure for importing data from
various sources (Google Takeout, Reddit, WhatsApp, etc.) into Potluck.

Public API:
    - register: Decorator to register an ingester class
    - detect_ingester: Find ingester matching a path's filename pattern
    - discover: Discover source type and available entities
    - ingest: Run the full ingestion pipeline

    - BaseIngester: Abstract base class for implementing ingesters
    - IngestionFilter: Date range filters for ingestion
    - DetectionResult: Result from detect_contents()

    - IngestionPipeline: Main orchestration class
    - IngestionResult: Result from running the pipeline
    - IngestionStats: Statistics from an ingestion run
    - DiscoveryResult: Result from discover()
"""

import re
from pathlib import Path

from sqlmodel import Session

from potluck.core.exceptions import ConfigurationError
from potluck.ingesters.base import (
    BaseIngester,
    DetectionResult,
    IngestionFilter,
)
from potluck.ingesters.pipeline import (
    DiscoveryResult,
    IngestionPipeline,
    IngestionResult,
    IngestionStats,
    ProgressCallback,
    discover,
)
from potluck.models.base import EntityType, SourceType

# Module-level registry of ingesters (simple dict, no singleton needed)
_INGESTERS: dict[SourceType, type[BaseIngester]] = {}


def register(cls: type[BaseIngester]) -> type[BaseIngester]:
    """Decorator to register an ingester class.

    Usage:
        @register
        class GoogleTakeoutIngester(BaseIngester):
            SOURCE_TYPE = SourceType.GOOGLE_TAKEOUT
            ...

    Args:
        cls: The ingester class to register.

    Returns:
        The same class (for decorator chaining).

    Raises:
        ConfigurationError: If the class does not define SOURCE_TYPE.
    """
    if not hasattr(cls, "SOURCE_TYPE"):
        raise ConfigurationError(f"{cls.__name__} must define SOURCE_TYPE class attribute")
    _INGESTERS[cls.SOURCE_TYPE] = cls
    return cls


def detect_ingester(path: Path) -> type[BaseIngester] | None:
    """Detect which ingester should handle the given path.

    Matches the path name against all registered ingester FILENAME_PATTERNS.
    Returns the first matching ingester.

    Args:
        path: Path to check (archive file or directory).

    Returns:
        The matching ingester class, or None if no match.
    """
    path_name = path.name

    for ingester in _INGESTERS.values():
        for pattern in ingester.FILENAME_PATTERNS:
            if re.match(pattern, path_name, re.IGNORECASE):
                return ingester

    return None


def get_ingester(source_type: SourceType) -> type[BaseIngester] | None:
    """Get an ingester by source type.

    Args:
        source_type: The source type to look up.

    Returns:
        The ingester class, or None if not registered.
    """
    return _INGESTERS.get(source_type)


def list_ingesters() -> list[type[BaseIngester]]:
    """List all registered ingesters.

    Returns:
        List of registered ingester classes.
    """
    return list(_INGESTERS.values())


def clear_registry() -> None:
    """Clear all registered ingesters.

    This is primarily for testing purposes.
    """
    _INGESTERS.clear()


def ingest(
    path: Path,
    session: Session,
    entity_types: set[EntityType] | None = None,
    filters: IngestionFilter | None = None,
    on_progress: ProgressCallback | None = None,
    resume_failed: bool = False,
) -> IngestionResult:
    """Run the ingestion pipeline for a path.

    This is a convenience function that creates an IngestionPipeline
    and runs it. Media entities are automatically queued for processing.

    Args:
        path: Path to source file or directory.
        session: Database session.
        entity_types: Entity types to ingest (None = all available).
        filters: Optional date range filters.
        on_progress: Optional progress callback (current, total, message).
        resume_failed: If True, retry failed entities from previous runs.

    Returns:
        IngestionResult with import run and statistics.
    """
    pipeline = IngestionPipeline(
        session=session,
        on_progress=on_progress,
    )
    return pipeline.run(
        path,
        entity_types=entity_types,
        filters=filters,
        resume_failed=resume_failed,
    )


__all__ = [
    # Registration
    "register",
    "detect_ingester",
    "get_ingester",
    "list_ingesters",
    "clear_registry",
    # Base classes
    "BaseIngester",
    "DetectionResult",
    "IngestionFilter",
    # Pipeline
    "IngestionPipeline",
    "IngestionResult",
    "IngestionStats",
    "DiscoveryResult",
    "ProgressCallback",
    "discover",
    "ingest",
]
