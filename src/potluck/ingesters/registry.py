"""Ingester registry for discovering and managing data source ingesters."""

import re
import threading
from pathlib import Path

from potluck.ingesters.utils.registry_base import BaseRegistry
from potluck.models.base import EntityType

from .base import BaseIngester

# Default file extension to EntityType mapping for generic content detection
# Ingesters can add to this via their SUPPORTED_EXTENSIONS class attribute
DEFAULT_EXTENSION_TO_ENTITY_TYPE: dict[str, EntityType] = {
    # Media files
    ".jpg": EntityType.MEDIA,
    ".jpeg": EntityType.MEDIA,
    ".png": EntityType.MEDIA,
    ".gif": EntityType.MEDIA,
    ".webp": EntityType.MEDIA,
    ".heic": EntityType.MEDIA,
    ".heif": EntityType.MEDIA,
    ".bmp": EntityType.MEDIA,
    ".tiff": EntityType.MEDIA,
    ".tif": EntityType.MEDIA,
    ".svg": EntityType.MEDIA,
    ".mp4": EntityType.MEDIA,
    ".mov": EntityType.MEDIA,
    ".avi": EntityType.MEDIA,
    ".mkv": EntityType.MEDIA,
    ".webm": EntityType.MEDIA,
    ".mp3": EntityType.MEDIA,
    ".wav": EntityType.MEDIA,
    ".flac": EntityType.MEDIA,
    ".m4a": EntityType.MEDIA,
    ".ogg": EntityType.MEDIA,
    # Text/notes
    ".txt": EntityType.KNOWLEDGE_NOTE,
    ".md": EntityType.KNOWLEDGE_NOTE,
    ".markdown": EntityType.KNOWLEDGE_NOTE,
    ".rst": EntityType.KNOWLEDGE_NOTE,
    # Email
    ".mbox": EntityType.EMAIL,
    ".eml": EntityType.EMAIL,
}


class IngesterRegistry(BaseRegistry[type[BaseIngester]]):
    """Registry for managing and discovering ingesters.

    The registry maintains a collection of registered ingester classes and
    provides methods for detecting which ingester should handle a given path.

    This is implemented as a thread-safe singleton to ensure consistent
    registration across the application.
    """

    _instance: "IngesterRegistry | None" = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "IngesterRegistry":
        """Create or return the singleton instance (thread-safe)."""
        return cls._create_singleton()  # type: ignore[return-value]

    def get_extension_map(self) -> dict[str, EntityType]:
        """Build extension map from defaults plus registered ingesters.

        Returns:
            Dict mapping file extensions to EntityType.
        """
        extensions = dict(DEFAULT_EXTENSION_TO_ENTITY_TYPE)
        with self._lock:
            for ingester in self._items:
                extensions.update(ingester.SUPPORTED_EXTENSIONS)
        return extensions

    def detect(self, path: Path) -> type[BaseIngester] | None:
        """Detect which ingester should handle the given path.

        Matches the path name against all registered ingester FILENAME_PATTERNS.
        Returns the first matching ingester.

        Args:
            path: Path to check (archive file or directory).

        Returns:
            The matching ingester class, or None if no match.
        """
        path_name = path.name

        with self._lock:
            for ingester in self._items:
                for pattern in ingester.FILENAME_PATTERNS:
                    if re.match(pattern, path_name, re.IGNORECASE):
                        return ingester

        return None

    def detect_generic(self, path: Path) -> dict[EntityType, int]:
        """Scan a path for generic content types.

        For paths that don't match any specific ingester, this method
        scans file extensions to determine what can be ingested.

        Args:
            path: Path to scan (directory or file).

        Returns:
            Dict mapping EntityType to count of files found.
        """
        extension_map = self.get_extension_map()
        counts: dict[EntityType, int] = {}

        if path.is_file():
            ext = path.suffix.lower()
            if ext in extension_map:
                entity_type = extension_map[ext]
                counts[entity_type] = 1
        elif path.is_dir():
            for file_path in path.rglob("*"):
                if file_path.is_file():
                    ext = file_path.suffix.lower()
                    if ext in extension_map:
                        entity_type = extension_map[ext]
                        counts[entity_type] = counts.get(entity_type, 0) + 1

        return counts


def get_registry() -> IngesterRegistry:
    """Get the global ingester registry instance.

    Returns:
        The singleton IngesterRegistry instance.
    """
    return IngesterRegistry()


def clear_registry() -> None:
    """Clear the registry singleton for testing.

    This clears all registered ingesters without destroying the singleton.
    """
    get_registry().clear()


def register_ingester(ingester: type[BaseIngester]) -> type[BaseIngester]:
    """Decorator to register an ingester class.

    Usage:
        @register_ingester
        class MyIngester(BaseIngester):
            ...

    Args:
        ingester: The ingester class to register.

    Returns:
        The same ingester class (for decorator chaining).
    """
    get_registry().register(ingester)
    return ingester
