"""Built-in benchmark scenarios.

P0 ships one placeholder that exercises the whole measured path (generators ->
writer thread -> executemany -> read back); real ingest/search scenarios with
hard budgets arrive with their features (P1+).

P1 scenarios
------------
- ingest_keep_2k / ingest_keep_8k (smoke): Keep Takeout ingest at 2k / 8k notes.
  Paired as a 1x vs 4x scaling check (O(n²) dedup would show t_8k >> 4 * t_2k).
- ingest_keep_10k (full): 10k-note ingest is the P1 budget anchor (< 30 s).
- reimport_noop_10k (full): second import of the same archive — pure dedup path
  (< 5 s).
- search_fts_10k (smoke): 100 mixed FTS queries over a 10k corpus.
  item_count = 100 (query count) so throughput_items_s = queries/s, not notes/s.

Architecture note
-----------------
``setup`` writes the archive (and, for reimport/search, the initial import) into
the workdir.  ``run`` opens a fresh AppContext pointing at the same workdir DB,
performs the measured work, then closes the DB.  No shared mutable state is held
across the two callables; the persisted SQLite file is the only shared artifact.
"""

import sqlite3
from collections.abc import Callable
from pathlib import Path

from potluck.bench.registry import Scenario, Tier
from potluck.core.config import Settings
from potluck.ingest.engine import run_import
from potluck.models.search import SearchRequest
from potluck.services.context import AppContext, create_context
from potluck.services.imports import import_path
from potluck.services.search import search
from potluck.storage.db import Database
from potluck.testing.generators import WORDS, synthetic_notes
from potluck.testing.keep import write_keep_takeout
from potluck.testing.mbox import TAIL_WORDS, synthetic_email_drafts, write_gmail_takeout

_NOTE_COUNT = 5000

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(workdir: Path) -> AppContext:
    """Open (or create) the bench DB inside *workdir* and return an AppContext."""
    return create_context(Settings(db_path=workdir / "bench.db"))


# Archive path is deterministic: write_keep_takeout always produces this name
# for a single-part zip inside dest_dir.
def _archive_path(workdir: Path) -> Path:
    return workdir / "archives" / "takeout-synth-001.zip"


# ---------------------------------------------------------------------------
# P0: meta round-trip (kept as regression anchor)
# ---------------------------------------------------------------------------


def _meta_roundtrip(workdir: Path) -> None:
    db = Database.open(workdir / "bench.db")
    try:
        rows = [
            (f"note:{i}", f"{note['title']}\n{note['text']}")
            for i, note in enumerate(synthetic_notes(_NOTE_COUNT))
        ]

        def insert(conn: sqlite3.Connection) -> None:
            conn.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", rows)

        db.write(insert)
        count = db.write(lambda c: int(c.execute("SELECT count(*) FROM meta").fetchone()[0]))
        if count != _NOTE_COUNT:
            raise RuntimeError(f"scenario integrity check failed: {count} != {_NOTE_COUNT}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# P1: Keep ingest scenarios
# ---------------------------------------------------------------------------


def _keep_ingest_scenario(name: str, tier: Tier, count: int) -> Scenario:
    """Factory for smoke/full Keep ingest scenarios at different corpus sizes."""

    def setup(workdir: Path) -> None:
        archive = write_keep_takeout(workdir / "archives", count, seed=42)
        if archive != _archive_path(workdir):
            raise RuntimeError(f"keep generator naming changed: {archive}")

    def run(workdir: Path) -> None:
        ctx = _make_ctx(workdir)
        try:
            import_path(ctx, _archive_path(workdir))
        finally:
            ctx.db.close()

    return Scenario(name=name, tier=tier, item_count=count, setup=setup, run=run)


# ---------------------------------------------------------------------------
# P1: No-op reimport scenario (dedup path)
# ---------------------------------------------------------------------------


def _imported_keep_10k_setup(workdir: Path) -> None:
    """Shared setup: generate a 10k Keep archive and import it once.

    Used by both reimport_noop_10k (run = second import) and search_fts_10k
    (run = query batch).
    """
    archive = write_keep_takeout(workdir / "archives", 10_000, seed=42)
    if archive != _archive_path(workdir):
        raise RuntimeError(f"keep generator naming changed: {archive}")
    ctx = _make_ctx(workdir)
    try:
        import_path(ctx, archive)
    finally:
        ctx.db.close()


def _reimport_noop_10k_run(workdir: Path) -> None:
    ctx = _make_ctx(workdir)
    try:
        import_path(ctx, _archive_path(workdir))
    finally:
        ctx.db.close()


# ---------------------------------------------------------------------------
# P1: FTS search scenario
# ---------------------------------------------------------------------------

# 100 deterministic queries: 50 single-word + 50 two-word, derived from WORDS.
# item_count for this scenario is the query count (100) so that throughput_items_s
# reports queries/s rather than notes/s — the meaningful unit for a search bench.
_SEARCH_QUERY_COUNT = 100
_SEARCH_QUERIES: tuple[str, ...] = tuple(
    [WORDS[i % len(WORDS)] for i in range(50)]
    + [f"{WORDS[i % len(WORDS)]} {WORDS[(i + 7) % len(WORDS)]}" for i in range(50)]
)


def _search_fts_10k_run(workdir: Path) -> None:
    ctx = _make_ctx(workdir)
    try:
        for q in _SEARCH_QUERIES:
            search(ctx, SearchRequest(query=q))
    finally:
        ctx.db.close()


# ---------------------------------------------------------------------------
# P2: Gmail ingest scenarios
# ---------------------------------------------------------------------------


def _gmail_ingest_scenario(name: str, tier: Tier, count: int) -> Scenario:
    """Factory for Gmail mbox-Takeout ingest scenarios (zip; real MIME parse)."""

    def setup(workdir: Path) -> None:
        write_gmail_takeout(workdir / "archives", count, seed=42)

    def run(workdir: Path) -> None:
        ctx = _make_ctx(workdir)
        try:
            import_path(ctx, _archive_path(workdir))
        finally:
            ctx.db.close()

    return Scenario(name=name, tier=tier, item_count=count, setup=setup, run=run)


def _imported_gmail_10k_setup(workdir: Path) -> None:
    """Generate a 10k Gmail Takeout zip and import it once (reimport setup)."""
    archive = write_gmail_takeout(workdir / "archives", 10_000, seed=42)
    ctx = _make_ctx(workdir)
    try:
        import_path(ctx, archive)
    finally:
        ctx.db.close()


# ---------------------------------------------------------------------------
# P2: search scenarios at scale (draft-fed corpora — no MIME round trip)
# ---------------------------------------------------------------------------


def _email_corpus_setup(count: int) -> Callable[[Path], None]:
    """Populate the bench DB with *count* email drafts through the real
    ingest engine (FTS path included) — tens of seconds at 250k, vs minutes
    for an mbox round trip."""

    def setup(workdir: Path) -> None:
        ctx = _make_ctx(workdir)
        try:
            run_import(
                ctx.db,
                source_name="gmail",
                parser_version=1,
                drafts=iter(synthetic_email_drafts(count, seed=42)),
                path="bench://drafts",
                file_hash=None,
            )
        finally:
            ctx.db.close()

    return setup


# Scenario workloads mirror the gated budgets: realistic selectivity
# (TAIL_WORDS tokens, rare+common pairs) — see test_p2_budgets.py for why
# pure stop-word queries are tracked separately rather than gated.
_FTS_SCALE_QUERIES: tuple[str, ...] = tuple(
    [TAIL_WORDS[(i * 37) % len(TAIL_WORDS)] for i in range(_SEARCH_QUERY_COUNT // 2)]
    + [
        f"{TAIL_WORDS[(i * 53) % len(TAIL_WORDS)]} {WORDS[i % len(WORDS)]}"
        for i in range(_SEARCH_QUERY_COUNT // 2)
    ]
)
# Selective SAYT prefixes: first word of a TAIL_WORDS compound + 2 chars of
# the second — never expands to a bare common word's dense doclist.
_TAIL_PAIRS: tuple[tuple[str, str], ...] = tuple((a, b) for a in WORDS for b in WORDS if a != b)
_PREFIX_QUERIES: tuple[str, ...] = tuple(
    [
        f"{_TAIL_PAIRS[(i * 37) % len(_TAIL_PAIRS)][0]}{_TAIL_PAIRS[(i * 37) % len(_TAIL_PAIRS)][1][:2]}"
        for i in range(_SEARCH_QUERY_COUNT // 2)
    ]
    + [
        f"{WORDS[i % len(WORDS)]} "
        f"{_TAIL_PAIRS[(i * 111) % len(_TAIL_PAIRS)][0]}{_TAIL_PAIRS[(i * 111) % len(_TAIL_PAIRS)][1][:2]}"
        for i in range(_SEARCH_QUERY_COUNT // 2)
    ]
)


def _search_run(workdir: Path, *, prefix: bool) -> None:
    queries = _PREFIX_QUERIES if prefix else _FTS_SCALE_QUERIES
    ctx = _make_ctx(workdir)
    try:
        for q in queries:
            search(ctx, SearchRequest(query=q, prefix=prefix))
    finally:
        ctx.db.close()


def _fts_run(workdir: Path) -> None:
    _search_run(workdir, prefix=False)


def _prefix_run(workdir: Path) -> None:
    _search_run(workdir, prefix=True)


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

ALL_SCENARIOS = [
    Scenario(name="meta_roundtrip_5k", tier="smoke", item_count=_NOTE_COUNT, run=_meta_roundtrip),
    # Keep ingest: smoke pair (2k × 8k) for scaling assertion + full 10k budget anchor
    _keep_ingest_scenario("ingest_keep_2k", "smoke", 2_000),
    _keep_ingest_scenario("ingest_keep_8k", "smoke", 8_000),
    _keep_ingest_scenario("ingest_keep_10k", "full", 10_000),
    # No-op reimport: pure dedup path budget (setup does first import; run does second)
    Scenario(
        name="reimport_noop_10k",
        tier="full",
        item_count=10_000,
        setup=_imported_keep_10k_setup,
        run=_reimport_noop_10k_run,
    ),
    # FTS search: item_count = query count so throughput = queries/s
    Scenario(
        name="search_fts_10k",
        tier="smoke",
        item_count=_SEARCH_QUERY_COUNT,
        setup=_imported_keep_10k_setup,
        run=_search_fts_10k_run,
    ),
    # P2 Gmail ingest: smoke pair (2k × 8k) for the 1x-vs-4x scaling gate;
    # the true 50k/5 GB budget is asserted by the nightly hard-budget tests
    # (1 rep, subprocess RSS) — the full-tier 50k scenario tracks the trend.
    _gmail_ingest_scenario("ingest_gmail_2k", "smoke", 2_000),
    _gmail_ingest_scenario("ingest_gmail_8k", "smoke", 8_000),
    _gmail_ingest_scenario("ingest_gmail_50k", "full", 50_000),
    # No-op gmail re-import: exercises file-hash + ledger short-circuit
    Scenario(
        name="reimport_noop_gmail_10k",
        tier="full",
        item_count=10_000,
        setup=_imported_gmail_10k_setup,
        run=_reimport_noop_10k_run,
    ),
    # P2 search at scale (item_count = query count → throughput = queries/s)
    Scenario(
        name="fts_p95_250k",
        tier="full",
        item_count=_SEARCH_QUERY_COUNT,
        setup=_email_corpus_setup(250_000),
        run=_fts_run,
    ),
    Scenario(
        name="prefix_p95_100k",
        tier="full",
        item_count=_SEARCH_QUERY_COUNT,
        setup=_email_corpus_setup(100_000),
        run=_prefix_run,
    ),
    # Cheap PR-tier SAYT latency tracking over the shared 10k Keep corpus
    Scenario(
        name="prefix_10k",
        tier="smoke",
        item_count=_SEARCH_QUERY_COUNT,
        setup=_imported_keep_10k_setup,
        run=_prefix_run,
    ),
]
