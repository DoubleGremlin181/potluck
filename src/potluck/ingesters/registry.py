"""Ingester registry for discovering and managing data source ingesters."""

import re
from pathlib import Path

from potluck.models.base import EntityType

from .base import BaseIngester

# File extension to EntityType mapping for generic content detection
EXTENSION_TO_ENTITY_TYPE: dict[str, EntityType] = {
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


class IngesterRegistry:
    """Registry for managing and discovering ingesters.

    The registry maintains a collection of registered ingester classes and
    provides methods for detecting which ingester should handle a given path.

    This is implemented as a singleton to ensure consistent registration
    across the application.
    """

    _instance: "IngesterRegistry | None" = None
    _ingesters: list[type[BaseIngester]]

    def __new__(cls) -> "IngesterRegistry":
        """Create or return the singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._ingesters = []
        return cls._instance

    def register(self, ingester: type[BaseIngester]) -> None:
        """Register an ingester class.

        Args:
            ingester: The ingester class to register.
        """
        if ingester not in self._ingesters:
            self._ingesters.append(ingester)

    def unregister(self, ingester: type[BaseIngester]) -> None:
        """Unregister an ingester class.

        Args:
            ingester: The ingester class to unregister.
        """
        if ingester in self._ingesters:
            self._ingesters.remove(ingester)

    def get_all(self) -> list[type[BaseIngester]]:
        """Get all registered ingesters.

        Returns:
            List of all registered ingester classes.
        """
        return list(self._ingesters)

    def detect(self, path: Path) -> type[BaseIngester] | None:
        """Detect which ingester should handle the given path.

        Matches the path name against all registered ingester DETECTION_PATTERNS.
        Returns the first matching ingester.

        Args:
            path: Path to check (archive file or directory).

        Returns:
            The matching ingester class, or None if no match.
        """
        path_name = path.name

        for ingester in self._ingesters:
            for pattern in ingester.DETECTION_PATTERNS:
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
        counts: dict[EntityType, int] = {}

        if path.is_file():
            ext = path.suffix.lower()
            if ext in EXTENSION_TO_ENTITY_TYPE:
                entity_type = EXTENSION_TO_ENTITY_TYPE[ext]
                counts[entity_type] = 1
        elif path.is_dir():
            for file_path in path.rglob("*"):
                if file_path.is_file():
                    ext = file_path.suffix.lower()
                    if ext in EXTENSION_TO_ENTITY_TYPE:
                        entity_type = EXTENSION_TO_ENTITY_TYPE[ext]
                        counts[entity_type] = counts.get(entity_type, 0) + 1

        return counts

    def clear(self) -> None:
        """Clear all registered ingesters. Useful for testing."""
        self._ingesters = []


def get_registry() -> IngesterRegistry:
    """Get the global ingester registry instance.

    Returns:
        The singleton IngesterRegistry instance.
    """
    return IngesterRegistry()


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
