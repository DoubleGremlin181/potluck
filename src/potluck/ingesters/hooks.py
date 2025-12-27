"""Ingestion hooks for post-processing entities."""

import threading
from typing import TYPE_CHECKING, Protocol

from potluck.core.logging import get_logger
from potluck.ingesters.utils.registry_base import BaseRegistry
from potluck.models.base import BaseEntity, EntityType

if TYPE_CHECKING:
    from potluck.models.sources import ImportRun

logger = get_logger(__name__)


class IngestionHook(Protocol):
    """Protocol for ingestion post-processing hooks.

    Hooks are called by the IngestionCoordinator during ingestion to allow
    additional processing such as:
    - Generating embeddings (Phase 5)
    - Creating entity links (Phase 9)
    - Sending notifications
    - Custom indexing

    Implementations should be lightweight and async-friendly where possible.
    Heavy processing should be queued as separate Celery tasks.
    """

    def on_entity_created(self, entity_type: EntityType, entity: BaseEntity) -> None:
        """Called when a new entity is created and persisted.

        Args:
            entity_type: The type of entity created.
            entity: The created entity instance.
        """
        ...

    def on_batch_complete(self, entities: dict[EntityType, list[BaseEntity]]) -> None:
        """Called when a batch of entities has been persisted.

        Args:
            entities: Dict mapping entity types to lists of created entities.
        """
        ...

    def on_import_complete(self, import_run: "ImportRun") -> None:
        """Called when the entire import run is complete.

        Args:
            import_run: The completed ImportRun record.
        """
        ...


class HookRegistry(BaseRegistry[IngestionHook]):
    """Registry for managing ingestion hooks.

    Maintains a list of registered hooks and dispatches events to them.
    Hooks are called in registration order.

    This is implemented as a thread-safe singleton.
    """

    _instance: "HookRegistry | None" = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "HookRegistry":
        """Create or return the singleton instance (thread-safe)."""
        return cls._create_singleton()  # type: ignore[return-value]

    def register(self, hook: IngestionHook) -> None:
        """Register an ingestion hook.

        Args:
            hook: The hook to register.
        """
        with self._lock:
            if hook not in self._items:
                self._items.append(hook)
                logger.debug(f"Registered ingestion hook: {hook.__class__.__name__}")

    def notify_entity_created(self, entity_type: EntityType, entity: BaseEntity) -> None:
        """Notify all hooks that an entity was created.

        Args:
            entity_type: The type of entity created.
            entity: The created entity.
        """
        # Copy hooks list to avoid issues if hook modifies registry
        hooks = self.get_all()
        for hook in hooks:
            try:
                hook.on_entity_created(entity_type, entity)
            except Exception as e:
                logger.warning(f"Hook {hook.__class__.__name__}.on_entity_created failed: {e}")

    def notify_batch_complete(self, entities: dict[EntityType, list[BaseEntity]]) -> None:
        """Notify all hooks that a batch is complete.

        Args:
            entities: Dict mapping entity types to lists of entities.
        """
        hooks = self.get_all()
        for hook in hooks:
            try:
                hook.on_batch_complete(entities)
            except Exception as e:
                logger.warning(f"Hook {hook.__class__.__name__}.on_batch_complete failed: {e}")

    def notify_import_complete(self, import_run: "ImportRun") -> None:
        """Notify all hooks that an import is complete.

        Args:
            import_run: The completed ImportRun.
        """
        hooks = self.get_all()
        for hook in hooks:
            try:
                hook.on_import_complete(import_run)
            except Exception as e:
                logger.warning(f"Hook {hook.__class__.__name__}.on_import_complete failed: {e}")


def get_hook_registry() -> HookRegistry:
    """Get the global hook registry instance.

    Returns:
        The singleton HookRegistry instance.
    """
    return HookRegistry()


def clear_hook_registry() -> None:
    """Clear the hook registry singleton for testing.

    This clears all registered hooks without destroying the singleton.
    """
    get_hook_registry().clear()


class LoggingHook:
    """Simple hook that logs ingestion events.

    Useful for debugging and development.
    """

    def on_entity_created(self, entity_type: EntityType, entity: BaseEntity) -> None:
        """Log entity creation."""
        logger.debug(f"Entity created: {entity_type.value} (id={entity.id})")

    def on_batch_complete(self, entities: dict[EntityType, list[BaseEntity]]) -> None:
        """Log batch completion."""
        counts = {et.value: len(ents) for et, ents in entities.items()}
        logger.info(f"Batch complete: {counts}")

    def on_import_complete(self, import_run: "ImportRun") -> None:
        """Log import completion."""
        logger.info(
            f"Import complete: run_id={import_run.id}, "
            f"created={import_run.entities_created}, "
            f"updated={import_run.entities_updated}, "
            f"skipped={import_run.entities_skipped}, "
            f"failed={import_run.entities_failed}"
        )
