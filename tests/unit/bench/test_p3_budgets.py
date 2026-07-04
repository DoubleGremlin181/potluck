"""P3 hard budgets (#131), nightly tier (@pytest.mark.bench).

Budget: REST search server time p95 < 100 ms @ 100k emails, measured
end-to-end through starlette's TestClient — in-process ASGI, so the timing
covers routing + param validation + the search service + response
serialization, with no network socket noise.

The workload mirrors the fts_p95 budgets in test_p2_budgets.py: realistic
selectivity (rare TAIL_WORDS tokens and rare+common pairs), not pure
stop-word queries.
"""

import statistics
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from potluck.api.app import create_app
from potluck.core.config import Settings
from potluck.ingest.engine import run_import
from potluck.services.context import create_context
from potluck.testing.generators import WORDS
from potluck.testing.mbox import TAIL_WORDS, synthetic_email_drafts

_QUERY_COUNT = 200
_BUDGET_S = 0.100


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
