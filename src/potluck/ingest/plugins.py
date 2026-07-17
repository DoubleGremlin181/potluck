"""Source plugin registry: declarative spec + auto-discovery.

A source plugin is a pure generator of typed Pydantic drafts.
The engine owns batching/hashing/dedup/FTS/progress/ledger.
"""

import fnmatch
import hashlib
import importlib
import logging
import pkgutil
import re
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import cached_property
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
    workers: parse worker processes for sources that parallelize decoding
    (#199); 0 = auto, 1 = sequential. Plugins may ignore it.
    """

    attachments_dir: Path | None = None
    workers: int = 0


type ParseFn = Callable[[Archive, ParseContext], Iterator[ItemDraft]]


@dataclass(frozen=True)
class Glob:
    """Detection pattern; fnmatch semantics ('*' crosses '/'), case-sensitive
    on every platform — archive member names are virtual posix paths.

    ``|`` separates alternative patterns, matched any-of (fnmatch itself has
    no alternation, and export layouts legitimately vary — e.g. WhatsApp's
    Android vs iOS naming). ``|`` is reserved as the separator: a literal
    ``|`` cannot be matched — splitting happens before fnmatch, so even a
    ``[|]`` character class is split apart. Real archive member names never
    contain one.
    """

    pattern: str

    @cached_property
    def _compiled(self) -> re.Pattern[str]:
        # detect_sources calls matches() for every member name × every
        # still-unmatched plugin — O(members × plugins) on single-source
        # archives (nothing ever exhausts `remaining`). Per-call splitting +
        # per-alternative fnmatchcase was ~40% of a Keep import's wall time
        # once P4 grew the registry to 14 plugins; one combined regex keeps
        # the whole check in C. fnmatch.translate is exactly fnmatchcase's
        # internal compilation, so semantics are unchanged.
        return re.compile(
            "|".join(f"(?:{fnmatch.translate(alt)})" for alt in self.pattern.split("|"))
        )

    def matches(self, name: str) -> bool:
        """Return True if *name* matches any of this glob's alternatives."""
        return self._compiled.match(name) is not None


@dataclass(frozen=True)
class SourcePlugin:
    """A fully-described source plugin, stored in the registry.

    generic (#150) marks the fallback tier: catch-all globs (``*.txt``,
    ``*.jpg``, ``*.mbox``) that only apply when NO specific plugin matched
    the archive — see :func:`detect_sources` for the tier rule.
    """

    name: str
    detect: Glob
    kinds: tuple[ItemKind, ...]
    parser_version: int
    parse: ParseFn
    generic: bool = False


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
    generic: bool = False,
) -> Callable[[ParseFn], ParseFn]:
    """Register a pure-generator parser as a Potluck source plugin.

    The engine owns batching/hashing/dedup/FTS/ledger; the decorated function
    only turns an Archive into typed drafts. ``generic=True`` places the
    plugin in the fallback detection tier (#150; see :func:`detect_sources`).

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
            generic=generic,
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


def detect_sources(archive: Archive) -> list[SourcePlugin]:
    """Single sequential pass over archive names; return EVERY matching plugin.

    A combined Takeout (Keep + Mail in one archive) surfaces every product it
    contains — the import layer runs one import per matched plugin (#195).
    Returned sorted by plugin name for a deterministic run order; empty list
    when nothing matches.

    Tier fallback (#150): when ANY specific plugin matched, every generic
    plugin is dropped — catch-all globs ('*' crosses '/') would otherwise
    double-import members a specific source already claims, and per-source
    identity means such collisions are real duplication, never dedup.
    Degradation to document at the source level: a messy folder holding both
    a recognized export and loose files imports only the export; importing
    the loose subfolder (or file) directly is the escape hatch.

    Tar-friendly: one sequential walk, exiting early once every registered
    plugin has matched.
    """
    plugins = discover()
    remaining = dict(sorted(plugins.items()))
    matched: list[SourcePlugin] = []

    for archive_name in archive.iter_names():
        for name in list(remaining):
            if remaining[name].detect.matches(archive_name):
                matched.append(remaining.pop(name))
        if not remaining:
            break

    if any(not plugin.generic for plugin in matched):
        dropped = sorted(plugin.name for plugin in matched if plugin.generic)
        if dropped:
            _logger.info(
                "generic source(s) %s suppressed: a specific source matched "
                "(import the loose folder/file directly to ingest what they cover)",
                ", ".join(dropped),
            )
        matched = [plugin for plugin in matched if not plugin.generic]
    matched.sort(key=lambda plugin: plugin.name)
    return matched


def registry_fingerprint(plugins: dict[str, SourcePlugin]) -> str:
    """Identity of the detection configuration: sorted plugin names + globs +
    tiers (canonical line ``name:glob:generic|specific``).

    detect_sources is a pure function of (archive names, this fingerprint) —
    the key that lets archive scans be cached (#196). The generic flag is
    included because it changes detection SEMANTICS (tier fallback, #150);
    parser_version is deliberately excluded: it changes what parse produces,
    not what matches. Adding the tier column invalidated pre-#150 cache
    entries once — a single cheap re-scan, never a correctness issue.
    """
    canonical = "\n".join(
        f"{name}:{plugin.detect.pattern}:{'generic' if plugin.generic else 'specific'}"
        for name, plugin in sorted(plugins.items())
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
