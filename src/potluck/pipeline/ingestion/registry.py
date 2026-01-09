"""Ingestion stage registry for discovering and managing ingestion stages."""

import re
from pathlib import Path

from potluck.core.exceptions import ConfigurationError
from potluck.models.base import SourceType
from potluck.pipeline.ingestion.base import BaseIngestionStage

# Module-level registry of ingestion stages
_STAGES: dict[SourceType, type[BaseIngestionStage]] = {}


def register(cls: type[BaseIngestionStage]) -> type[BaseIngestionStage]:
    """Decorator to register an ingestion stage class.

    Usage:
        @register
        class GoogleTakeoutStage(BaseIngestionStage):
            SOURCE_TYPE = SourceType.GOOGLE_TAKEOUT
            ...

    Args:
        cls: The ingestion stage class to register.

    Returns:
        The same class (for decorator chaining).

    Raises:
        ConfigurationError: If the class does not define SOURCE_TYPE.
    """
    if not hasattr(cls, "SOURCE_TYPE"):
        raise ConfigurationError(f"{cls.__name__} must define SOURCE_TYPE class attribute")
    _STAGES[cls.SOURCE_TYPE] = cls
    return cls


def detect_stage(path: Path) -> type[BaseIngestionStage] | None:
    """Detect which ingestion stage should handle the given path.

    Matches the path name against all registered stage FILENAME_PATTERNS.
    Returns the first matching stage.

    Args:
        path: Path to check (archive file or directory).

    Returns:
        The matching stage class, or None if no match.
    """
    path_name = path.name

    for stage in _STAGES.values():
        for pattern in stage.FILENAME_PATTERNS:
            if re.match(pattern, path_name, re.IGNORECASE):
                return stage

    return None


def get_stage(source_type: SourceType) -> type[BaseIngestionStage] | None:
    """Get an ingestion stage by source type.

    Args:
        source_type: The source type to look up.

    Returns:
        The stage class, or None if not registered.
    """
    return _STAGES.get(source_type)


def list_stages() -> list[type[BaseIngestionStage]]:
    """List all registered ingestion stages.

    Returns:
        List of registered stage classes.
    """
    return list(_STAGES.values())


def clear_registry() -> None:
    """Clear all registered ingestion stages.

    This is primarily for testing purposes.
    """
    _STAGES.clear()
