"""Source plugin registry: declarative spec + auto-discovery.

A source plugin is a pure generator of typed Pydantic drafts.
The engine owns batching/hashing/dedup/FTS/progress/ledger.
"""

import fnmatch
import importlib
import logging
import pkgutil
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import potluck.ingest.sources as sources_pkg
from potluck.core.errors import DuplicateSourceError
from potluck.ingest.readers import Archive
from potluck.models.drafts import ItemDraft
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParseContext:
    """Engine-provided context passed to every parse function.

    attachments_dir: managed root for content-addressed blob extraction, or
    None when extraction is disabled (the default — metadata only).
    """

    attachments_dir: Path | None = None


type ParseFn = Callable[[Archive, ParseContext], Iterator[ItemDraft]]


@dataclass(frozen=True)
class Glob:
    """Detection pattern; fnmatch semantics ('*' crosses '/'), case-sensitive
    on every platform — archive member names are virtual posix paths."""

    pattern: str

    def matches(self, name: str) -> bool:
        """Return True if *name* matches this glob pattern."""
        return fnmatch.fnmatchcase(name, self.pattern)


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

    A module that fails to import (e.g. a half-edited ``dev new-source``
    scaffold with a SyntaxError) is logged and skipped — one broken plugin
    must not break every import. ``dev check-source`` reports the details.
    """
    for module_info in pkgutil.iter_modules(sources_pkg.__path__):
        full_name = f"potluck.ingest.sources.{module_info.name}"
        if full_name not in sys.modules:
            try:
                importlib.import_module(full_name)
            except Exception as exc:
                _logger.warning(
                    "skipping broken source module %s: %s (run 'potluck dev "
                    "check-source %s' for details)",
                    full_name,
                    exc,
                    module_info.name,
                )

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
