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


def _import_subprocess(archive: Path, data_home: Path) -> tuple[int, int, int, float, int]:
    env = dict(os.environ, XDG_DATA_HOME=str(data_home), XDG_CONFIG_HOME=str(data_home / "cfg"))
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
