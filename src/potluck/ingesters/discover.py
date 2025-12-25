"""Auto-detection of data source types for ingestion."""

from dataclasses import dataclass, field
from pathlib import Path

from potluck.core.logging import get_logger
from potluck.ingesters.base import BaseIngester
from potluck.ingesters.registry import get_registry
from potluck.ingesters.utils.archive import extracted
from potluck.models.base import EntityType

logger = get_logger(__name__)


@dataclass
class DiscoveryResult:
    """Result of discovering what can be ingested from a path.

    Contains the detected ingester and available entity types.
    """

    ingester: type[BaseIngester] | None
    """The ingester class to use (None for generic sources)."""

    is_generic: bool
    """True if no specific ingester pattern matched."""

    available_entities: dict[EntityType, int]
    """Entity types found with counts."""

    source_path: Path
    """Original path provided."""

    extract_path: Path | None = None
    """Path to extracted contents (if source was an archive)."""

    metadata: dict[str, str] = field(default_factory=dict)
    """Additional metadata about the detected content."""

    @property
    def total_entities(self) -> int:
        """Get total count of all entities."""
        return sum(self.available_entities.values())

    @property
    def has_content(self) -> bool:
        """Check if any ingestable content was found."""
        return bool(self.available_entities)


def discover(path: Path) -> DiscoveryResult:
    """Auto-detect what type of export a file/folder is.

    This is Level 1 detection - it identifies the source type (e.g., Google Takeout,
    Reddit, WhatsApp) and scans for available entity types.

    The detection process:
    1. If path is an archive, extract to temp directory first
    2. Match path/filename against all registered DETECTION_PATTERNS
    3. If match found: return matched ingester and call detect_contents()
    4. If no pattern matches: scan file extensions as generic source

    Args:
        path: Path to the file or directory to analyze.

    Returns:
        DiscoveryResult with ingester and available entities.

    Raises:
        FileNotFoundError: If the path does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")

    logger.info(f"Discovering content in: {path}")

    registry = get_registry()

    # Check if path matches a specific ingester pattern
    ingester = registry.detect(path)

    if ingester is not None:
        logger.info(f"Detected source type: {ingester.SOURCE_TYPE.value}")
        return _discover_with_ingester(path, ingester)

    # No specific pattern matched - try generic detection
    logger.info("No specific source pattern matched, trying generic detection")
    return _discover_generic(path)


def _discover_with_ingester(
    path: Path,
    ingester: type[BaseIngester],
) -> DiscoveryResult:
    """Discover content using a specific ingester.

    Args:
        path: Path to the source.
        ingester: Ingester class to use.

    Returns:
        DiscoveryResult with entity counts.
    """
    # Use the extracted context manager to handle archives
    with extracted(path) as content_path:
        # Create an instance and detect contents
        ingester_instance = ingester()
        detection_result = ingester_instance.detect_contents(content_path)

        return DiscoveryResult(
            ingester=ingester,
            is_generic=False,
            available_entities=detection_result.entity_counts,
            source_path=path,
            extract_path=content_path if content_path != path else None,
            metadata=detection_result.metadata,
        )


def _discover_generic(path: Path) -> DiscoveryResult:
    """Discover content as a generic source.

    Scans file extensions to determine what can be ingested.

    Args:
        path: Path to the source.

    Returns:
        DiscoveryResult with entity counts.
    """
    registry = get_registry()

    # Use extracted context to handle archives
    with extracted(path) as content_path:
        # Scan file extensions
        entity_counts = registry.detect_generic(content_path)

        return DiscoveryResult(
            ingester=None,  # Will use generic ingester
            is_generic=True,
            available_entities=entity_counts,
            source_path=path,
            extract_path=content_path if content_path != path else None,
        )


def discover_async(path: Path) -> DiscoveryResult:
    """Async version of discover (currently just calls sync version).

    Provided for API consistency with other async operations.

    Args:
        path: Path to analyze.

    Returns:
        DiscoveryResult with ingester and available entities.
    """
    # For now, discovery is synchronous since it's I/O bound on local files
    # In the future, this could use asyncio for parallel file scanning
    return discover(path)


def get_ingester_for_source(source_type: str) -> type[BaseIngester] | None:
    """Get an ingester class by source type name.

    Args:
        source_type: Source type value (e.g., 'google_takeout', 'reddit').

    Returns:
        Matching ingester class or None if not found.
    """
    registry = get_registry()

    for ingester in registry.get_all():
        if ingester.SOURCE_TYPE.value == source_type:
            return ingester

    return None


def list_available_sources() -> list[dict[str, str | list[str]]]:
    """List all available data sources and their instructions.

    Returns:
        List of dicts with source info (type, name, entity_types, instructions).
    """
    registry = get_registry()
    sources = []

    for ingester in registry.get_all():
        sources.append(
            {
                "type": ingester.SOURCE_TYPE.value,
                "name": ingester.SOURCE_TYPE.name.replace("_", " ").title(),
                "entity_types": [et.value for et in ingester.SUPPORTED_ENTITY_TYPES],
                "instructions": ingester.INSTRUCTIONS,
            }
        )

    return sources
