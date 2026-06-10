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
from pathlib import Path

from potluck.bench.registry import Scenario, Tier
from potluck.core.config import Settings
from potluck.models.search import SearchRequest
from potluck.services.context import AppContext, create_context
from potluck.services.imports import import_path
from potluck.services.search import search
from potluck.storage.db import Database
from potluck.testing.generators import WORDS, synthetic_notes
from potluck.testing.keep import write_keep_takeout

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


def _reimport_noop_10k_setup(workdir: Path) -> None:
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


def _search_fts_10k_setup(workdir: Path) -> None:
    archive = write_keep_takeout(workdir / "archives", 10_000, seed=42)
    if archive != _archive_path(workdir):
        raise RuntimeError(f"keep generator naming changed: {archive}")
    ctx = _make_ctx(workdir)
    try:
        import_path(ctx, archive)
    finally:
        ctx.db.close()


def _search_fts_10k_run(workdir: Path) -> None:
    ctx = _make_ctx(workdir)
    try:
        for q in _SEARCH_QUERIES:
            search(ctx, SearchRequest(query=q))
    finally:
        ctx.db.close()


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
        setup=_reimport_noop_10k_setup,
        run=_reimport_noop_10k_run,
    ),
    # FTS search: item_count = query count so throughput = queries/s
    Scenario(
        name="search_fts_10k",
        tier="smoke",
        item_count=_SEARCH_QUERY_COUNT,
        setup=_search_fts_10k_setup,
        run=_search_fts_10k_run,
    ),
]
