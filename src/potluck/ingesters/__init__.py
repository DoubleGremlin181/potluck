"""Data source ingesters for Potluck."""

from potluck.ingesters.base import (
    ENTITY_TYPE_METHOD_MAP,
    BaseIngester,
    DetectionResult,
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
    IngesterRegistry,
    get_registry,
    register_ingester,
)

__all__ = [
    # Base classes
    "BaseIngester",
    "DetectionResult",
    "ENTITY_TYPE_METHOD_MAP",
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
]
