"""Source plugin registry: declarative spec + auto-discovery.

A source plugin is a pure generator of typed Pydantic drafts.
The engine owns batching/hashing/dedup/FTS/progress/ledger.
"""

import fnmatch
import importlib
import pkgutil
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

import potluck.ingest.sources as sources_pkg
from potluck.core.errors import DuplicateSourceError
from potluck.ingest.readers import Archive
from potluck.models.drafts import ItemDraft
from potluck.models.items import ItemKind

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

type ParseFn = Callable[[Archive], Iterator[ItemDraft]]


@dataclass(frozen=True)
class Glob:
    """Detection pattern; fnmatch semantics ('*' crosses '/')."""

    pattern: str

    def matches(self, name: str) -> bool:
        """Return True if *name* matches this glob pattern."""
        return fnmatch.fnmatch(name, self.pattern)


@dataclass(frozen=True)
class SourcePlugin:
    """A fully-described source plugin, stored in the registry."""

    name: str
    detect: Glob
    kinds: tuple[ItemKind, ...]
    parser_version: int
    parse: ParseFn


# ---------------------------------------------------------------------------
# Module-level registry
# ---------------------------------------------------------------------------

# Decorator execution is serialized by Python's import lock; calling @source
# outside module import in concurrent contexts is unsupported.
_registry: dict[str, SourcePlugin] = {}


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def source(
    *,
    name: str,
    detect: Glob,
    kinds: Sequence[ItemKind],
    parser_version: int = 1,
) -> Callable[[ParseFn], ParseFn]:
    """Register a pure-generator parser as a Potluck source plugin.

    The engine owns batching/hashing/dedup/FTS/ledger; the decorated function
    only turns an Archive into typed drafts.

    Raises DuplicateSourceError on name collision.
    """

    def decorator(fn: ParseFn) -> ParseFn:
        if name in _registry:
            raise DuplicateSourceError(
                f"source plugin '{name}' is already registered; each source name must be unique"
            )
        _registry[name] = SourcePlugin(
            name=name,
            detect=detect,
            kinds=tuple(kinds),
            parser_version=parser_version,
            parse=fn,
        )
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover() -> dict[str, SourcePlugin]:
    """Import every module in potluck.ingest.sources; return a name→plugin snapshot.

    Importing executes @source decorators which fill the registry.
    Idempotent: modules already in sys.modules are not re-imported.
    """
    for module_info in pkgutil.iter_modules(sources_pkg.__path__):
        full_name = f"potluck.ingest.sources.{module_info.name}"
        if full_name not in sys.modules:
            importlib.import_module(full_name)

    return dict(_registry)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_source(archive: Archive) -> SourcePlugin | None:
    """Single sequential pass over archive names; return the first matching plugin.

    Precedence rules:
    - The first archive member (in archive order) to match any plugin wins.
    - If one member matches multiple plugins, the lexicographically smallest
      plugin name is returned.

    Returns None when no plugin recognises the archive.
    Tar-friendly: a single sequential walk with early exit.
    """
    plugins = discover()
    sorted_names = sorted(plugins.keys())

    for archive_name in archive.iter_names():
        for plugin_name in sorted_names:
            if plugins[plugin_name].detect.matches(archive_name):
                return plugins[plugin_name]

    return None
