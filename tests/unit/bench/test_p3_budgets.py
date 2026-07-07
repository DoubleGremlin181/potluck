"""P3 hard budgets, nightly tier (@pytest.mark.bench).

- #131: REST search server time p95 < 100 ms @ 100k emails, measured
  end-to-end through starlette's TestClient — in-process ASGI, so the timing
  covers routing + param validation + the search service + response
  serialization, with no network socket noise.  The workload mirrors the
  fts_p95 budgets in test_p2_budgets.py: realistic selectivity (rare
  TAIL_WORDS tokens and rare+common pairs), not pure stop-word queries.
- #141: quickstart budgets over a real ``potluck serve`` subprocess —
  cold start (spawn → first healthy /api/health) < 2 s, and SPA cold load
  (GET / + every referenced JS/CSS asset, real localhost socket) < 1 s.
"""

import statistics
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from potluck.api.app import create_app
from potluck.core.config import Settings
from potluck.ingest.engine import run_import
from potluck.services.context import create_context
from potluck.testing.generators import WORDS
from potluck.testing.mbox import TAIL_WORDS, synthetic_email_drafts
from potluck.testing.server import free_port, spawn_serve, wait_for_health
from potluck.testing.spa import referenced_assets, write_spa_dist

_QUERY_COUNT = 200
_BUDGET_S = 0.100
_COLD_START_BUDGET_S = 2.0
_SPA_LOAD_BUDGET_S = 1.0
# Medians over small trial counts: cold start pays a full process spawn per
# trial (3 keeps the nightly job cheap); the SPA rounds are ~100 ms each.
_COLD_START_TRIALS = 3
_SPA_LOAD_ROUNDS = 5


def _realistic_queries(count: int) -> list[str]:
    tail = [TAIL_WORDS[(i * 37) % len(TAIL_WORDS)] for i in range(count)]
    queries = tail[: count // 2]
    queries += [f"{t} {WORDS[i % len(WORDS)]}" for i, t in enumerate(tail[count // 2 :])]
    return queries


@pytest.mark.bench
def test_budget_api_search_p95_under_100ms_at_100k(tmp_path: Path) -> None:
    ctx = create_context(
        # web_dist pinned to a nonexistent dir: the budget measures the API,
        # not whether a SPA build happens to be lying around.
        Settings(db_path=tmp_path / "bench.db", web_dist=tmp_path / "no-spa")
    )
    try:
        run_import(
            ctx.db,
            source_name="gmail",
            parser_version=1,
            drafts=iter(synthetic_email_drafts(100_000, seed=42)),
            path="bench://drafts",
            file_hash=None,
        )
        times: list[float] = []
        with TestClient(create_app(ctx)) as client:
            for q in _realistic_queries(_QUERY_COUNT):
                started = time.perf_counter()
                resp = client.get("/api/search", params={"q": q})
                times.append(time.perf_counter() - started)
                assert resp.status_code == 200, resp.text
        p95 = statistics.quantiles(times, n=100)[94]
        assert p95 < _BUDGET_S, f"API search p95 {p95 * 1000:.1f} ms at 100k (budget 100 ms)"
    finally:
        ctx.db.close()


def _cold_start_once(workdir: Path) -> float:
    """One spawn → first healthy /api/health measurement (kill excluded)."""
    workdir.mkdir()
    port = free_port()
    started = time.perf_counter()
    proc = spawn_serve(workdir, port)
    try:
        wait_for_health(port, proc)
        return time.perf_counter() - started
    finally:
        proc.kill()
        proc.wait(timeout=10)


@pytest.mark.bench
def test_budget_serve_cold_start_under_2s(tmp_path: Path) -> None:
    """#141: `uvx … potluck serve` must answer /api/health within 2 s of
    process start — interpreter boot, imports, DB creation, uvicorn bind."""
    times = [_cold_start_once(tmp_path / f"trial{i}") for i in range(_COLD_START_TRIALS)]
    median = statistics.median(times)
    assert median < _COLD_START_BUDGET_S, (
        f"serve cold start median {median:.2f}s over {_COLD_START_TRIALS} trials "
        f"(budget {_COLD_START_BUDGET_S}s); trials: {[round(t, 2) for t in times]}"
    )


@pytest.mark.bench
def test_budget_spa_cold_load_under_1s(tmp_path: Path) -> None:
    """#141: fetching / plus every referenced JS/CSS asset — the requests a
    browser must complete before first render — takes < 1 s against a real
    server over a real localhost socket.  Each round uses a fresh connection
    (no keep-alive reuse), and the synthetic build mirrors the real bundle's
    weight (~700 KB; see testing/spa.py)."""
    dist = write_spa_dist(tmp_path / "dist")
    port = free_port()
    proc = spawn_serve(tmp_path, port, web_dist=dist)
    try:
        wait_for_health(port, proc)
        times: list[float] = []
        for _ in range(_SPA_LOAD_ROUNDS):
            started = time.perf_counter()
            with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
                index = client.get("/")
                index.raise_for_status()
                assets = referenced_assets(index.text)
                assert len(assets) == 2, assets
                for asset in assets:
                    client.get(asset).raise_for_status()
            times.append(time.perf_counter() - started)
        median = statistics.median(times)
        assert median < _SPA_LOAD_BUDGET_S, (
            f"SPA cold load median {median * 1000:.0f} ms over {_SPA_LOAD_ROUNDS} rounds "
            f"(budget {_SPA_LOAD_BUDGET_S * 1000:.0f} ms)"
        )
    finally:
        proc.kill()
        proc.wait(timeout=10)
