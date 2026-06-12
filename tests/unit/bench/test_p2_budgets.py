"""P2 hard budgets (#126), nightly tier (@pytest.mark.bench).

Budgets (4-core CPU CI-class):
- 50k-email (~5 GB mbox) cold ingest < 600 s, peak RSS < 1.5 GB
- no-op re-run of the same archive < 60 s (ledger short-circuit)
- superset re-import ingests only the delta at 10k -> 12k

The import runs in a SUBPROCESS so ru_maxrss reflects the import alone, and
the no-op re-run pays the honest cost (interpreter start, file hash of the
full archive) in a fresh process.
"""

import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from potluck.testing.mbox import MBOX_MEMBER, write_gmail_takeout

_COUNT = 50_000
_BODY_KB = 100  # 50k x ~100 KB ≈ 5 GB of mbox
_INGEST_BUDGET_S = 600
_RSS_BUDGET_KB = 1_572_864  # 1.5 GB
_NOOP_BUDGET_S = 60

_DRIVER = """
import resource, sys, time
from pathlib import Path
from potluck.services.context import create_context
from potluck.services.imports import import_path

ctx = create_context()
started = time.perf_counter()
runs = import_path(ctx, Path(sys.argv[1]))
elapsed = time.perf_counter() - started
ctx.db.close()
rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # KB on Linux
[run] = runs
print(f"{run.id} {run.items_new} {run.items_duplicate} {elapsed:.2f} {rss_kb}")
"""


def _import_subprocess(
    archive: Path, data_home: Path, extra_env: dict[str, str] | None = None
) -> tuple[int, int, int, float, int]:
    env = dict(os.environ, XDG_DATA_HOME=str(data_home), XDG_CONFIG_HOME=str(data_home / "cfg"))
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, "-c", _DRIVER, str(archive)],
        capture_output=True,
        text=True,
        check=True,
        timeout=_INGEST_BUDGET_S * 2,
        env=env,
    )
    run_id, new, dup, elapsed, rss_kb = proc.stdout.split()
    return int(run_id), int(new), int(dup), float(elapsed), int(rss_kb)


def _zip_streaming(corpus_root: Path, dest: Path) -> Path:
    """Zip the dir-format Takeout without ever holding the mbox in memory
    (ZipFile.write streams from disk)."""
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(corpus_root.rglob("*")):
            if file.is_file():
                zf.write(file, arcname=str(file.relative_to(corpus_root)))
    return dest


@pytest.mark.bench
def test_budget_gmail_50k_ingest_and_noop(tmp_path: Path) -> None:
    corpus = write_gmail_takeout(tmp_path / "gen", _COUNT, seed=42, fmt="dir", body_kb=_BODY_KB)
    mbox_size = (corpus / MBOX_MEMBER).stat().st_size
    assert mbox_size > 4 * 1024**3, f"corpus should be ~5 GB, got {mbox_size / 1024**3:.1f} GB"
    archive = _zip_streaming(corpus, tmp_path / "takeout-50k.zip")

    data_home = tmp_path / "data"
    run_id, new, dup, elapsed, rss_kb = _import_subprocess(archive, data_home)
    assert new == _COUNT
    assert elapsed < _INGEST_BUDGET_S, (
        f"50k ingest took {elapsed:.0f}s (budget {_INGEST_BUDGET_S}s)"
    )
    assert rss_kb < _RSS_BUDGET_KB, f"peak RSS {rss_kb / 1024:.0f} MB (budget 1536 MB)"

    # No-op re-run: same file, same parser -> ledger short-circuit.
    started = time.perf_counter()
    rerun_id, rerun_new, _, _, _ = _import_subprocess(archive, data_home)
    noop_elapsed = time.perf_counter() - started
    assert rerun_id == run_id, "expected the short-circuit to return the prior run"
    assert rerun_new == new  # the prior ledger row, counters untouched
    assert noop_elapsed < _NOOP_BUDGET_S, (
        f"no-op re-run took {noop_elapsed:.0f}s (budget {_NOOP_BUDGET_S}s)"
    )


@pytest.mark.bench
def test_budget_superset_delta_10k_to_12k(tmp_path: Path) -> None:
    from potluck.services.context import create_context
    from potluck.services.imports import import_path

    os.environ["XDG_DATA_HOME"] = str(tmp_path / "data")
    os.environ["XDG_CONFIG_HOME"] = str(tmp_path / "cfg")
    try:
        ctx = create_context()
        small = write_gmail_takeout(tmp_path / "small", 10_000, seed=42)
        [run1] = import_path(ctx, small)
        assert run1.items_new == 10_000

        large = write_gmail_takeout(tmp_path / "large", 12_000, seed=42)
        [run2] = import_path(ctx, large)
        assert run2.items_new == 2_000
        assert run2.items_duplicate == 10_000
        ctx.db.close()
    finally:
        os.environ.pop("XDG_DATA_HOME", None)
        os.environ.pop("XDG_CONFIG_HOME", None)


# ---------------------------------------------------------------------------
# Search p95 hard budgets (#130): FTS < 100 ms @ 250k, prefix < 50 ms @ 100k
# ---------------------------------------------------------------------------


def _p95_over_queries(ctx: object, queries: list[str], *, prefix: bool) -> float:
    import statistics

    from potluck.models.search import SearchRequest
    from potluck.services.context import AppContext
    from potluck.services.search import search

    assert isinstance(ctx, AppContext)
    times: list[float] = []
    for q in queries:
        started = time.perf_counter()
        search(ctx, SearchRequest(query=q, prefix=prefix))
        times.append(time.perf_counter() - started)
    return statistics.quantiles(times, n=100)[94]


def _email_corpus_ctx(tmp_path: Path, count: int) -> object:
    from potluck.core.config import Settings
    from potluck.ingest.engine import run_import
    from potluck.services.context import create_context
    from potluck.testing.mbox import synthetic_email_drafts

    ctx = create_context(Settings(db_path=tmp_path / "bench.db"))
    run_import(
        ctx.db,
        source_name="gmail",
        parser_version=1,
        drafts=iter(synthetic_email_drafts(count, seed=42)),
        path="bench://drafts",
        file_hash=None,
    )
    return ctx


def _selective_prefix(i: int) -> str:
    """A SAYT prefix that expands only to long-tail tokens: the full first
    word of a TAIL_WORDS compound plus 2 chars of the second — by construction
    never equal to (or a prefix-parent of) a bare common word's doclist."""
    from potluck.testing.generators import WORDS

    pairs = [(a, b) for a in WORDS for b in WORDS if a != b]
    a, b = pairs[(i * 37) % len(pairs)]
    return f"{a}{b[:2]}"


def _realistic_fts_queries(count: int) -> list[str]:
    """Realistic-selectivity workload: rare single tokens (~0.4% of docs) and
    rare+common pairs. Pure stop-word queries (every WORDS term matches ~40%
    of a synthetic corpus — a density no real vocabulary has) are measured
    separately below, not gated at the interactive budget."""
    from potluck.testing.generators import WORDS
    from potluck.testing.mbox import TAIL_WORDS

    tail = [TAIL_WORDS[(i * 37) % len(TAIL_WORDS)] for i in range(count)]
    queries = tail[: count // 2]
    queries += [f"{t} {WORDS[i % len(WORDS)]}" for i, t in enumerate(tail[count // 2 :])]
    return queries


@pytest.mark.bench
def test_budget_fts_p95_under_100ms_at_250k(tmp_path: Path) -> None:
    from potluck.testing.generators import WORDS

    ctx = _email_corpus_ctx(tmp_path, 250_000)
    try:
        p95 = _p95_over_queries(ctx, _realistic_fts_queries(200), prefix=False)
        assert p95 < 0.100, f"FTS p95 {p95 * 1000:.1f} ms at 250k (budget 100 ms)"

        # Worst case, tracked not budgeted: a term matching ~40% of the corpus
        # ranks ~100k rows (~300k rows/s bm25+sort). Known FTS5 characteristic;
        # the generous lid only catches true blowups. Revisit in P5.
        dense = _p95_over_queries(ctx, [WORDS[i % len(WORDS)] for i in range(20)], prefix=False)
        assert dense < 1.0, f"dense-term p95 {dense * 1000:.0f} ms — worst case exploded"
    finally:
        ctx.db.close()  # type: ignore[attr-defined]


@pytest.mark.bench
def test_budget_prefix_p95_under_50ms_at_100k(tmp_path: Path) -> None:
    from potluck.testing.generators import WORDS

    ctx = _email_corpus_ctx(tmp_path, 100_000)
    try:
        # SAYT steady state: enough typed characters that the prefix expands
        # only to distinctive tokens. A prefix whose expansion INCLUDES a
        # common term ("gar*" -> "garden") degenerates to dense ranking — that
        # worst case is tracked under the lid below, not gated (FTS5 has no
        # top-k early termination; revisit in P5).
        selective = [_selective_prefix(i) for i in range(100)]
        pairs = [f"{WORDS[i % len(WORDS)]} {_selective_prefix(i * 3)}" for i in range(100)]
        p95 = _p95_over_queries(ctx, selective + pairs, prefix=True)
        assert p95 < 0.050, f"prefix p95 {p95 * 1000:.1f} ms at 100k (budget 50 ms)"

        # Worst case (3-char prefix of a common word), tracked not budgeted.
        dense = _p95_over_queries(ctx, [WORDS[i % len(WORDS)][:3] for i in range(20)], prefix=True)
        assert dense < 1.0, f"dense-prefix p95 {dense * 1000:.0f} ms — worst case exploded"
    finally:
        ctx.db.close()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Scaling gate (#130): gmail ingest must stay near-linear (1x vs 4x)
# ---------------------------------------------------------------------------


@pytest.mark.bench
def test_scaling_gmail_ingest_near_linear(tmp_path: Path) -> None:
    from potluck.testing.mbox import write_gmail_takeout

    def _timed_import(count: int, name: str) -> float:
        archive = write_gmail_takeout(tmp_path / name, count, seed=42)
        data_home = tmp_path / f"data-{name}"
        _, new, _, elapsed, _ = _import_subprocess(archive, data_home)
        assert new == count
        return elapsed

    t_2k = max(_timed_import(2_000, "g2k"), 0.05)  # floor avoids flaky ratios
    t_8k = _timed_import(8_000, "g8k")
    assert t_8k < 4 * 1.5 * t_2k, (
        f"8k ingest {t_8k:.2f}s vs 2k {t_2k:.2f}s — worse than 1.5x linear; O(n^2) path?"
    )


# ---------------------------------------------------------------------------
# Pool A/B gate (#199): rule-3 evidence that parallel parsing earns its keep
# ---------------------------------------------------------------------------


@pytest.mark.bench
def test_budget_pooled_parse_beats_sequential(tmp_path: Path) -> None:
    """Pooled MIME parsing (workers=auto) must beat sequential (workers=1)
    by a conservative margin on the 4-core CI class. Measured locally:
    ~2.9x at 2k messages including pool startup."""
    archive = write_gmail_takeout(tmp_path / "corpus", 8_000, seed=42)

    _, n_seq, _, t_seq, _ = _import_subprocess(
        archive, tmp_path / "data-seq", extra_env={"POTLUCK_INGEST_WORKERS": "1"}
    )
    _, n_pool, _, t_pool, _ = _import_subprocess(archive, tmp_path / "data-pool")
    assert n_seq == n_pool == 8_000
    assert t_pool < 0.8 * t_seq, (
        f"pooled import {t_pool:.1f}s vs sequential {t_seq:.1f}s — expected >=1.25x"
    )
