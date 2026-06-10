"""P1 performance budget tests.

Marked ``@pytest.mark.bench`` — excluded from default test runs.
Run explicitly with::

    uv run pytest -m bench -q

These tests measure real end-to-end latency against hard P1 budgets on 4-core
CI-class hardware.  Each test is self-contained (its own archive, its own DB)
so they can run in any order without shared mutable state.

Budgets
-------
- Ingest 10k notes < 30 s
- No-op reimport (pure dedup) < 5 s
- FTS p95 < 50 ms at 10k corpus
- 8k ingest < 4 × 1.5 × 2k ingest (near-linear scaling; catches O(n²) dedup)

The nightly CI workflow runs both ``potluck bench run --tier full`` (scenario
runner, baseline compare) and ``pytest -m bench`` (these hard budget assertions).
"""

import statistics
import time
from pathlib import Path

import pytest

from potluck.core.config import Settings
from potluck.models.search import SearchRequest
from potluck.services.context import create_context
from potluck.services.imports import import_path
from potluck.services.search import search
from potluck.testing.generators import WORDS
from potluck.testing.keep import write_keep_takeout

pytestmark = pytest.mark.bench

# ---------------------------------------------------------------------------
# P1 budget constants
# ---------------------------------------------------------------------------
_INGEST_10K_BUDGET_S = 30.0
_NOOP_REIMPORT_BUDGET_S = 5.0
_SEARCH_P95_BUDGET_S = 0.050  # 50 ms

# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

# 200 deterministic queries: 100 single-word + 100 two-word from WORDS.
# Enough data points for statistics.quantiles(n=100)[94] to be meaningful.
_FTS_QUERY_COUNT = 200
_FTS_QUERIES: tuple[str, ...] = tuple(
    [WORDS[i % len(WORDS)] for i in range(100)]
    + [f"{WORDS[i % len(WORDS)]} {WORDS[(i + 7) % len(WORDS)]}" for i in range(100)]
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_budget_ingest_10k_under_30s(tmp_path: Path) -> None:
    """10k Keep archive ingest must complete in < 30 s (P1 budget)."""
    # Build corpus (untimed)
    archive = write_keep_takeout(tmp_path / "archive", 10_000, seed=42)
    settings = Settings(db_path=tmp_path / "bench.db")
    ctx = create_context(settings)
    try:
        t0 = time.perf_counter()
        import_path(ctx, archive)
        elapsed = time.perf_counter() - t0
    finally:
        ctx.db.close()

    assert elapsed < _INGEST_10K_BUDGET_S, (
        f"10k ingest took {elapsed:.1f}s — budget is {_INGEST_10K_BUDGET_S}s"
    )


def test_budget_noop_reimport_under_5s(tmp_path: Path) -> None:
    """Second import of the same 10k archive (pure dedup path) must be < 5 s."""
    archive = write_keep_takeout(tmp_path / "archive", 10_000, seed=42)
    settings = Settings(db_path=tmp_path / "bench.db")
    ctx = create_context(settings)
    try:
        import_path(ctx, archive)  # first import — untimed setup
    finally:
        ctx.db.close()

    # Fresh context for the timed run (matches the reimport_noop_10k scenario:
    # cold connection, dedup path only).
    ctx = create_context(settings)
    try:
        t0 = time.perf_counter()
        import_path(ctx, archive)  # second import — timed (dedup path)
        elapsed = time.perf_counter() - t0
    finally:
        ctx.db.close()

    assert elapsed < _NOOP_REIMPORT_BUDGET_S, (
        f"no-op reimport took {elapsed:.1f}s — budget is {_NOOP_REIMPORT_BUDGET_S}s"
    )


def test_budget_search_p95_under_50ms_at_10k(tmp_path: Path) -> None:
    """FTS p95 query latency must be < 50 ms at a 10k-note corpus."""
    archive = write_keep_takeout(tmp_path / "archive", 10_000, seed=42)
    settings = Settings(db_path=tmp_path / "bench.db")
    ctx = create_context(settings)
    try:
        import_path(ctx, archive)  # populate corpus (untimed)
        query_times: list[float] = []
        for q in _FTS_QUERIES:
            t0 = time.perf_counter()
            search(ctx, SearchRequest(query=q))
            query_times.append(time.perf_counter() - t0)
    finally:
        ctx.db.close()

    # statistics.quantiles(n=100)[94] gives the 95th percentile
    p95 = statistics.quantiles(query_times, n=100)[94]
    assert p95 < _SEARCH_P95_BUDGET_S, (
        f"FTS p95 = {p95 * 1000:.1f}ms — budget is {_SEARCH_P95_BUDGET_S * 1000:.0f}ms"
    )


def test_scaling_ingest_near_linear(tmp_path: Path) -> None:
    """Ingest must scale near-linearly: t_8k < 4 × 1.5 × t_2k (catches O(n²) dedup)."""
    # Build both archives (untimed)
    archive_2k = write_keep_takeout(tmp_path / "archive_2k", 2_000, seed=42)
    archive_8k = write_keep_takeout(tmp_path / "archive_8k", 8_000, seed=42)

    # Time 2k import on a fresh DB
    ctx_2k = create_context(Settings(db_path=tmp_path / "bench_2k.db"))
    try:
        t0 = time.perf_counter()
        import_path(ctx_2k, archive_2k)
        t_2k = time.perf_counter() - t0
    finally:
        ctx_2k.db.close()

    # Time 8k import on a separate fresh DB
    ctx_8k = create_context(Settings(db_path=tmp_path / "bench_8k.db"))
    try:
        t0 = time.perf_counter()
        import_path(ctx_8k, archive_8k)
        t_8k = time.perf_counter() - t0
    finally:
        ctx_8k.db.close()

    # Floor t_2k to avoid flaky ratios when the machine runs it in < 50ms
    t_2k_floored = max(t_2k, 0.05)
    budget = 4 * t_2k_floored * 1.5
    assert t_8k < budget, (
        f"Scaling looks super-linear: 2k={t_2k:.3f}s, 8k={t_8k:.3f}s, "
        f"ratio={t_8k / t_2k_floored:.2f}x (budget: <{4 * 1.5:.1f}x)"
    )
