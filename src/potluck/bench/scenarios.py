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

from fastapi.testclient import TestClient

from potluck.api.app import create_app
from potluck.bench.registry import Scenario, Tier
from potluck.core.config import Settings
from potluck.ingest.engine import run_import
from potluck.models.search import SearchRequest
from potluck.services.context import AppContext, create_context
from potluck.services.imports import import_path
from potluck.services.search import search
from potluck.storage.db import Database
from potluck.testing.chrome import write_chrome_takeout
from potluck.testing.generators import WORDS, synthetic_notes
from potluck.testing.keep import write_keep_takeout
from potluck.testing.mbox import TAIL_WORDS, synthetic_email_drafts, write_gmail_takeout
from potluck.testing.server import free_port, spawn_serve, wait_for_health
from potluck.testing.spa import referenced_assets, write_spa_dist
from potluck.testing.whatsapp import write_whatsapp_export

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


def _gmail_ingest_scenario(name: str, tier: Tier, count: int, *, workers: int = 0) -> Scenario:
    """Factory for Gmail mbox-Takeout ingest scenarios (zip; real MIME parse).

    *workers* feeds Settings.ingest_workers: the default 0 (auto pool) and an
    explicit 1 (sequential) form the #199 rule-3 A/B pair.
    """

    def setup(workdir: Path) -> None:
        write_gmail_takeout(workdir / "archives", count, seed=42)

    def run(workdir: Path) -> None:
        ctx = create_context(Settings(db_path=workdir / "bench.db", ingest_workers=workers))
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
# Smoke-tier SAYT prefixes for the shared 10k KEEP corpus, whose vocabulary is
# bare WORDS — the TAIL_WORDS compounds above can never match there, and a
# zero-hit workload measures only empty term-range scans (never bm25 ranking,
# snippet(), or row fetch). Each prefix is a WORD minus its final character
# (a user one keystroke from a real token), so every query hits; guarded by
# test_prefix_10k_smoke_queries_hit_the_keep_corpus.
_KEEP_PREFIX_QUERIES: tuple[str, ...] = tuple(
    [WORDS[i % len(WORDS)][:-1] for i in range(_SEARCH_QUERY_COUNT // 2)]
    + [
        f"{WORDS[i % len(WORDS)]} {WORDS[(i + 7) % len(WORDS)][:-1]}"
        for i in range(_SEARCH_QUERY_COUNT // 2)
    ]
)


def _search_run(workdir: Path, queries: tuple[str, ...], *, prefix: bool) -> None:
    ctx = _make_ctx(workdir)
    try:
        for q in queries:
            search(ctx, SearchRequest(query=q, prefix=prefix))
    finally:
        ctx.db.close()


def _fts_run(workdir: Path) -> None:
    _search_run(workdir, _FTS_SCALE_QUERIES, prefix=False)


def _prefix_run(workdir: Path) -> None:
    _search_run(workdir, _PREFIX_QUERIES, prefix=True)


def _keep_prefix_run(workdir: Path) -> None:
    _search_run(workdir, _KEEP_PREFIX_QUERIES, prefix=True)


# ---------------------------------------------------------------------------
# P4: WhatsApp ingest scenarios (#142)
# ---------------------------------------------------------------------------


def _whatsapp_ingest_scenario(name: str, tier: Tier, per_chat: int, chats: int) -> Scenario:
    """Factory for WhatsApp chat-export ingest scenarios (zip; real txt parse).

    *chats* spreads the corpus over ("us", "eu") locale dialects so both
    timestamp paths are always measured; item_count = per_chat x chats
    (logical export lines — the parser skips the 5% system lines).
    """

    def setup(workdir: Path) -> None:
        archive = write_whatsapp_export(
            workdir / "archives",
            per_chat,
            seed=42,
            locales=("us", "eu"),
            chats_per_locale=chats // 2,
        )
        if archive != workdir / "archives" / "whatsapp-synth-001.zip":
            raise RuntimeError(f"whatsapp generator naming changed: {archive}")

    def run(workdir: Path) -> None:
        ctx = _make_ctx(workdir)
        try:
            import_path(ctx, workdir / "archives" / "whatsapp-synth-001.zip")
        finally:
            ctx.db.close()

    return Scenario(name=name, tier=tier, item_count=per_chat * chats, setup=setup, run=run)


# ---------------------------------------------------------------------------
# P4: Chrome history ingest scenarios (#145)
# ---------------------------------------------------------------------------


def _chrome_ingest_scenario(name: str, tier: Tier, count: int) -> Scenario:
    """Factory for Chrome Takeout history ingest scenarios (zip; real
    incremental-JSON parse); item_count = history records (every record
    imports — verbatim duplicates land via #N identity suffixes)."""

    def setup(workdir: Path) -> None:
        archive = write_chrome_takeout(workdir / "archives", count, seed=42)
        if archive != workdir / "archives" / "chrome-synth-001.zip":
            raise RuntimeError(f"chrome generator naming changed: {archive}")

    def run(workdir: Path) -> None:
        ctx = _make_ctx(workdir)
        try:
            import_path(ctx, workdir / "archives" / "chrome-synth-001.zip")
        finally:
            ctx.db.close()

    return Scenario(name=name, tier=tier, item_count=count, setup=setup, run=run)


# ---------------------------------------------------------------------------
# P3: end-to-end REST search (#131)
# ---------------------------------------------------------------------------


def _api_search_run(workdir: Path) -> None:
    """Drive GET /api/search through starlette's TestClient — in-process
    ASGI, so the measurement is server time (routing + param validation +
    search service + JSON serialization) without network-socket noise.
    Reuses _FTS_SCALE_QUERIES: the same realistic-selectivity workload the
    service-level fts scenarios measure, over the same email corpus."""
    ctx = _make_ctx(workdir)
    try:
        with TestClient(create_app(ctx)) as client:
            for q in _FTS_SCALE_QUERIES:
                resp = client.get("/api/search", params={"q": q})
                resp.raise_for_status()
    finally:
        ctx.db.close()


# ---------------------------------------------------------------------------
# P3: serve cold start + SPA cold load (#141)
# ---------------------------------------------------------------------------


def _serve_cold_start_run(workdir: Path) -> None:
    """Real ``potluck serve`` subprocess: spawn -> first 200 from /api/health.

    This is the beta.1 quickstart moment (interpreter start, imports, DB
    open, uvicorn bind), so it must be a process, not an in-process app.
    Teardown is a hard kill on purpose: nothing is being written (fresh empty
    DB, no import running) and SIGKILL + wait costs ~1 ms, so graceful
    shutdown time never pollutes the cold-start measurement.
    """
    port = free_port()
    proc = spawn_serve(workdir, port)
    try:
        wait_for_health(port, proc)
    finally:
        proc.kill()
        proc.wait(timeout=10)


def _spa_cold_load_setup(workdir: Path) -> None:
    write_spa_dist(workdir / "dist")


def _spa_cold_load_run(workdir: Path) -> None:
    """Fetch / plus every JS/CSS asset index.html references — the requests a
    browser must complete before first render, over the synthetic build that
    mirrors the real bundle's weight (see testing/spa.py).  In-process ASGI
    like the api_search scenarios: server time without socket noise (the
    nightly budget test re-measures over a real localhost socket)."""
    ctx = create_context(Settings(db_path=workdir / "bench.db", web_dist=workdir / "dist"))
    try:
        with TestClient(create_app(ctx)) as client:
            index = client.get("/")
            index.raise_for_status()
            assets = referenced_assets(index.text)
            if len(assets) != 2:
                raise RuntimeError(f"expected 2 referenced assets, found {assets}")
            for asset in assets:
                resp = client.get(asset)
                resp.raise_for_status()
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
    # #199 rule-3 evidence: sequential twin of ingest_gmail_8k — the pooled
    # default must beat this (gated in test_p2_budgets.py).
    _gmail_ingest_scenario("ingest_gmail_8k_seq", "full", 8_000, workers=1),
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
    # (Keep-vocabulary prefixes — every query returns real hits there)
    Scenario(
        name="prefix_10k",
        tier="smoke",
        item_count=_SEARCH_QUERY_COUNT,
        setup=_imported_keep_10k_setup,
        run=_keep_prefix_run,
    ),
    # P4 WhatsApp ingest (#142): smoke tracker + the full-tier 100k corpus.
    # The < 2 min hard budget is asserted nightly in test_p4_budgets.py
    # (1 rep, subprocess); the full-tier scenario tracks the trend.
    _whatsapp_ingest_scenario("ingest_whatsapp_5k", "smoke", 2_500, 2),
    _whatsapp_ingest_scenario("ingest_whatsapp_100k", "full", 25_000, 4),
    # P4 Chrome history ingest (#145): smoke tracker + the full-tier 200k
    # corpus. The < 2 min hard budget is asserted nightly in
    # test_p4_budgets.py (1 rep, subprocess, peak-RSS gate); the full-tier
    # scenario tracks the trend.
    _chrome_ingest_scenario("ingest_chrome_10k", "smoke", 10_000),
    _chrome_ingest_scenario("ingest_chrome_200k", "full", 200_000),
    # P3 REST search end-to-end (#131): nightly 100k budget anchor (p95
    # < 100 ms, asserted in test_p3_budgets.py) + smoke 10k PR-CI tracker
    Scenario(
        name="api_search_p95_100k",
        tier="full",
        item_count=_SEARCH_QUERY_COUNT,
        setup=_email_corpus_setup(100_000),
        run=_api_search_run,
    ),
    Scenario(
        name="api_search_10k",
        tier="smoke",
        item_count=_SEARCH_QUERY_COUNT,
        setup=_email_corpus_setup(10_000),
        run=_api_search_run,
    ),
    # P3 quickstart budgets (#141): item_count=1 for both, so
    # throughput_items_s reads as cold starts (loads) per second.  Budgets
    # (serve < 2 s, SPA load < 1 s) are asserted in test_p3_budgets.py.
    Scenario(name="serve_cold_start", tier="smoke", item_count=1, run=_serve_cold_start_run),
    Scenario(
        name="spa_cold_load",
        tier="smoke",
        item_count=1,
        setup=_spa_cold_load_setup,
        run=_spa_cold_load_run,
    ),
]
