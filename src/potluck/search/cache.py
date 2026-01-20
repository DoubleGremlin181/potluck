"""Search results caching with simple invalidation.

Provides in-memory caching of SearchResults with all-or-nothing invalidation
when any write operation occurs on searchable entities.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from potluck.search.dtos import SearchResults


@dataclass
class CacheEntry:
    """A cached search result with metadata."""

    results: SearchResults
    cached_at: datetime


class SearchCache:
    """In-memory LRU-style cache for search results.

    Uses a simple dictionary with TTL-based expiration.
    Invalidation is all-or-nothing - clear_all() removes everything.

    Thread-safety note: This cache is not thread-safe. For multi-threaded
    environments, external synchronization is required.
    """

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 300) -> None:
        """Initialize the cache.

        Args:
            max_size: Maximum number of entries to cache.
            ttl_seconds: Time-to-live for cache entries in seconds.
        """
        self._cache: dict[str, CacheEntry] = {}
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _make_key(
        query: str,
        entity_types: frozenset[str],
        mode: str,
        limit: int,
        offset: int,
        since: datetime | None,
        until: datetime | None,
        source_types: frozenset[str] | None,
    ) -> str:
        """Generate a cache key from search parameters.

        Args:
            query: The search query string.
            entity_types: Frozen set of entity type values.
            mode: Search mode value.
            limit: Result limit.
            offset: Result offset.
            since: Start datetime filter.
            until: End datetime filter.
            source_types: Optional frozen set of source type values.

        Returns:
            SHA256 hash of the parameters as a cache key.
        """
        # Create a deterministic string representation of all parameters
        key_parts = [
            query,
            "|".join(sorted(entity_types)),
            mode,
            str(limit),
            str(offset),
            since.isoformat() if since else "",
            until.isoformat() if until else "",
            "|".join(sorted(source_types)) if source_types else "",
        ]
        key_string = "::".join(key_parts)
        return hashlib.sha256(key_string.encode()).hexdigest()

    def get(
        self,
        query: str,
        entity_types: frozenset[str],
        mode: str,
        limit: int,
        offset: int,
        since: datetime | None,
        until: datetime | None,
        source_types: frozenset[str] | None,
    ) -> SearchResults | None:
        """Retrieve cached results if available and not expired.

        Args:
            query: The search query string.
            entity_types: Frozen set of entity type values.
            mode: Search mode value.
            limit: Result limit.
            offset: Result offset.
            since: Start datetime filter.
            until: End datetime filter.
            source_types: Optional frozen set of source type values.

        Returns:
            Cached SearchResults if found and valid, None otherwise.
        """
        key = self._make_key(query, entity_types, mode, limit, offset, since, until, source_types)
        entry = self._cache.get(key)

        if entry is None:
            return None

        # Check TTL
        age_seconds = (datetime.now() - entry.cached_at).total_seconds()
        if age_seconds > self._ttl_seconds:
            del self._cache[key]
            return None

        return entry.results

    def set(
        self,
        query: str,
        entity_types: frozenset[str],
        mode: str,
        limit: int,
        offset: int,
        since: datetime | None,
        until: datetime | None,
        source_types: frozenset[str] | None,
        results: SearchResults,
    ) -> None:
        """Store search results in the cache.

        Args:
            query: The search query string.
            entity_types: Frozen set of entity type values.
            mode: Search mode value.
            limit: Result limit.
            offset: Result offset.
            since: Start datetime filter.
            until: End datetime filter.
            source_types: Optional frozen set of source type values.
            results: The SearchResults to cache.
        """
        # Evict oldest entries if at capacity
        if len(self._cache) >= self._max_size:
            self._evict_oldest()

        key = self._make_key(query, entity_types, mode, limit, offset, since, until, source_types)
        self._cache[key] = CacheEntry(results=results, cached_at=datetime.now())

    def _evict_oldest(self) -> None:
        """Remove the oldest entry from the cache."""
        if not self._cache:
            return

        oldest_key = min(self._cache, key=lambda k: self._cache[k].cached_at)
        del self._cache[oldest_key]

    def clear_all(self) -> None:
        """Clear all cache entries.

        This is the invalidation strategy - when any write occurs to a
        searchable entity, clear the entire cache.
        """
        self._cache.clear()

    def size(self) -> int:
        """Return the current number of cached entries."""
        return len(self._cache)

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "ttl_seconds": self._ttl_seconds,
        }


# Global cache instance - shared across the application
_search_cache: SearchCache | None = None


def get_search_cache() -> SearchCache:
    """Get the global search cache instance.

    Returns:
        The singleton SearchCache instance.
    """
    global _search_cache
    if _search_cache is None:
        _search_cache = SearchCache()
    return _search_cache


def invalidate_search_cache() -> None:
    """Invalidate the search cache.

    Call this when any write operation occurs on a searchable entity.
    """
    cache = get_search_cache()
    cache.clear_all()
