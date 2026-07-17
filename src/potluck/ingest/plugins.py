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
        # Hot path: detection tests member names against globs for every
        # archive member. Per-call splitting + per-alternative fnmatchcase
        # was ~40% of a Keep import's wall time once P4 grew the registry to
        # 14 plugins; one combined regex keeps the whole check in C.
        # fnmatch.translate is exactly fnmatchcase's internal compilation, so
        # semantics are unchanged. detect_sources additionally unions these
        # compiled patterns across plugins (see _union_regex) so its
        # per-member steady state is ONE C-level match, not one per plugin.
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


def _union_regex(remaining: dict[str, SourcePlugin]) -> re.Pattern[str] | None:
    """One regex matching iff ANY remaining plugin's glob matches; None when empty.

    detect_sources' steady state is a member name that matches NO remaining
    plugin (a single-source archive never exhausts ``remaining``), so the
    per-member cost must not scale with registry size. Reusing each Glob's
    already-translated pattern keeps semantics bit-identical to calling
    ``matches()`` per plugin; the union is rebuilt only when a plugin matches
    (at most once per registered plugin per scan).
    """
    if not remaining:
        return None
    return re.compile(
        "|".join(f"(?:{plugin.detect._compiled.pattern})" for plugin in remaining.values())
    )


_WILDCARD_SPLIT_RE: re.Pattern[str] = re.compile(r"[*?]")


def _required_literal(alt: str) -> str | None:
    """The longest wildcard-free run of glob alternative *alt*, or None.

    Any name the alternative matches must CONTAIN every wildcard-free run
    (wildcards only add characters around the runs), so the longest run is a
    safe containment prefilter. An alternative with a character class is not
    parsed (``[x]`` matches ``x`` — bracketed text is not literal): returning
    None disables prefiltering rather than risking a wrong literal. So does
    an all-wildcard alternative (no literal to require).
    """
    if "[" in alt:
        return None
    return max(_WILDCARD_SPLIT_RE.split(alt), key=len) or None


def _prefilter_regex(remaining: dict[str, SourcePlugin]) -> re.Pattern[str] | None:
    """Literal-containment prefilter for the union regex; None = cannot filter.

    A regex of plain ``re.escape``-d literals, one per glob alternative of
    every remaining plugin. Invariant: ``prefilter.search(name) is None``
    implies ``_union_regex(remaining).match(name) is None`` — a miss proves
    no remaining plugin matches. Pure-literal alternation fails ~5x faster
    than the translated-glob union (measured 0.7 µs vs 3.6 µs per name at 12
    plugins: sre's first-character charset scan vs per-branch ``.*`` heads),
    and the steady state is exactly that miss. None (some alternative has no
    usable literal) means the caller must run the union on every name.
    """
    literals: set[str] = set()
    for plugin in remaining.values():
        for alt in plugin.detect.pattern.split("|"):
            lit = _required_literal(alt)
            if lit is None:
                return None
            literals.add(lit)
    return re.compile("|".join(map(re.escape, sorted(literals))))


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

    # Three-stage check, cheapest first; the per-member steady state (a name
    # matching NO remaining plugin) must not scale with registry size:
    #   1. literal prefilter — a miss proves no remaining plugin matches;
    #   2. union regex — answers "does ANY remaining plugin match?" in one
    #      C-level call (also caps the cost of rare prefilter false
    #      positives: a name containing a literal but matching no glob);
    #   3. per-plugin loop — finds WHICH, only on true hits. Every hit
    #      removes at least one plugin, so stages 1-2 are rebuilt at most
    #      len(plugins) times per scan.
    union = _union_regex(remaining)
    prefilter = _prefilter_regex(remaining) if union is not None else None
    for archive_name in archive.iter_names():
        # None ⇔ nothing left to match. Checked INSIDE the loop so an empty
        # registry still pulls the first name — reading must start so a
        # corrupt archive raises (→ UnsupportedArchiveError upstream) instead
        # of reporting "no source plugin recognises" (behaviour pinned by
        # test_background_failure_corrupt_archive).
        if union is None:
            break
        if prefilter is not None and prefilter.search(archive_name) is None:
            continue
        if union.match(archive_name) is None:
            continue
        for name in list(remaining):
            if remaining[name].detect.matches(archive_name):
                matched.append(remaining.pop(name))
        union = _union_regex(remaining)
        if union is None:
            # Early exit: every plugin matched — stop before pulling the next
            # name (single-pass contract; served-count pinned by test).
            break
        prefilter = _prefilter_regex(remaining)

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
