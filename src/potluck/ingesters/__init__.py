"""Data source ingesters for Potluck."""

from potluck.ingesters.base import (
    BaseIngester,
    DetectionResult,
    EntityCount,
    IngestionFilter,
    IngestMethod,
)
from potluck.ingesters.coordinator import IngestionCoordinator, IngestionResult
from potluck.ingesters.discover import (
    DiscoveryResult,
    discover,
    get_ingester_for_source,
    list_available_sources,
)
from potluck.ingesters.hooks import (
    HookRegistry,
    IngestionHook,
    LoggingHook,
    get_hook_registry,
)
from potluck.ingesters.registry import (
    EXTENSION_TO_ENTITY_TYPE,
    IngesterRegistry,
    get_registry,
    register_ingester,
)

__all__ = [
    # Base classes
    "BaseIngester",
    "DetectionResult",
    "EntityCount",
    "IngestionFilter",
    "IngestMethod",
    # Coordinator
    "IngestionCoordinator",
    "IngestionResult",
    # Discovery
    "DiscoveryResult",
    "discover",
    "get_ingester_for_source",
    "list_available_sources",
    # Hooks
    "HookRegistry",
    "IngestionHook",
    "LoggingHook",
    "get_hook_registry",
    # Registry
    "IngesterRegistry",
    "get_registry",
    "register_ingester",
    "EXTENSION_TO_ENTITY_TYPE",
]
