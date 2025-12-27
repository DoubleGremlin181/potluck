"""Base registry class for thread-safe singleton registries."""

import threading


class BaseRegistry[T]:
    """Base class for thread-safe singleton registries.

    Provides common functionality for managing a collection of items with
    thread-safe singleton pattern. Subclasses should:
    1. Define their own _instance and _lock class attributes
    2. Override __new__ to call _create_singleton()
    3. Add any domain-specific methods

    Example:
        class MyRegistry(BaseRegistry[MyItem]):
            _instance: "MyRegistry | None" = None
            _lock: threading.Lock = threading.Lock()

            def __new__(cls) -> "MyRegistry":
                return cls._create_singleton()
    """

    _instance: "BaseRegistry[T] | None" = None
    _lock: threading.Lock = threading.Lock()
    _items: list[T]

    @classmethod
    def _create_singleton(cls) -> "BaseRegistry[T]":
        """Create or return the singleton instance (thread-safe).

        Uses double-checked locking to ensure only one instance is created
        even under concurrent access.

        Returns:
            The singleton instance.
        """
        if cls._instance is None:
            with cls._lock:
                # Double-check after acquiring lock
                if cls._instance is None:
                    instance = object.__new__(cls)
                    instance._items = []
                    cls._instance = instance
        return cls._instance

    def register(self, item: T) -> None:
        """Register an item.

        Args:
            item: The item to register.
        """
        with self._lock:
            if item not in self._items:
                self._items.append(item)

    def unregister(self, item: T) -> None:
        """Unregister an item.

        Args:
            item: The item to unregister.
        """
        with self._lock:
            if item in self._items:
                self._items.remove(item)

    def get_all(self) -> list[T]:
        """Get all registered items.

        Returns:
            List of all registered items.
        """
        with self._lock:
            return list(self._items)

    def clear(self) -> None:
        """Clear all registered items. Useful for testing."""
        with self._lock:
            self._items = []
