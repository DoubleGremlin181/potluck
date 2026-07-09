"""P4 hard budgets (#142, #145), nightly tier (@pytest.mark.bench).

Budgets (4-core CPU CI-class):
- 100k-message WhatsApp export cold ingest < 120 s (#142)
- 200k-visit Chrome history cold ingest < 120 s, peak RSS < 800 MB (#145 —
  the RSS gate keeps the incremental-JSON memory posture honest: the parse
  holds the decoded member text plus one record, never the whole tree)

Each import runs in a SUBPROCESS (pattern from test_p2_budgets.py): a fresh
interpreter pays the honest cost — start, imports, DB creation, archive
hash — and its measurement is isolated from the pytest process. Peak RSS is
read from the child's own VmHWM, not ru_maxrss (a child's ru_maxrss is
polluted by the spawning pytest process's resident set; mechanism documented
in test_p2_mbox_rss.py).
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from potluck.testing.chrome import expected_visit_count, write_chrome_takeout
from potluck.testing.whatsapp import expected_message_count, write_whatsapp_export

_PER_CHAT = 25_000
_CHATS = 4
_INGEST_BUDGET_S = 120

_CHROME_COUNT = 200_000
# Measured 533 MiB at 200k: ~256 MB is bulk_import_mode's sqlite page cache,
# ~130 MB the one decoded member (bytes + text) — the parse itself holds one
# record at a time. A whole-tree json.load would add ~80 MB more, growing
# linearly with history size; this lid would catch that regression.
_CHROME_RSS_BUDGET_KB = 819_200  # 800 MB

_DRIVER = """
import sys, time
from pathlib import Path
from potluck.services.context import create_context
from potluck.services.imports import import_path

ctx = create_context()
started = time.perf_counter()
runs = import_path(ctx, Path(sys.argv[1]))
elapsed = time.perf_counter() - started
ctx.db.close()
with open("/proc/self/status") as f:
    rss_kb = next(int(line.split()[1]) for line in f if line.startswith("VmHWM:"))
[run] = runs
print(f"{run.items_new} {run.items_duplicate} {elapsed:.2f} {rss_kb}")
"""


def _import_subprocess(archive: Path, data_home: Path) -> tuple[int, int, float, int]:
    # Scrub inherited POTLUCK_* (see test_p2_budgets.py for the mechanism):
    # the autouse isolated_dirs fixture pins POTLUCK_DB_PATH in the pytest
    # process, and env beats the XDG-derived default.
    env = {k: v for k, v in os.environ.items() if not k.startswith("POTLUCK_")}
    env.update(XDG_DATA_HOME=str(data_home), XDG_CONFIG_HOME=str(data_home / "cfg"))
    proc = subprocess.run(
        [sys.executable, "-c", _DRIVER, str(archive)],
        capture_output=True,
        text=True,
        check=True,
        timeout=_INGEST_BUDGET_S * 2,
        env=env,
    )
    new, dup, elapsed, rss_kb = proc.stdout.split()
    return int(new), int(dup), float(elapsed), int(rss_kb)


@pytest.mark.bench
def test_budget_whatsapp_100k_ingest(tmp_path: Path) -> None:
    generated = time.perf_counter()
    archive = write_whatsapp_export(
        tmp_path / "gen", _PER_CHAT, seed=42, locales=("us", "eu"), chats_per_locale=_CHATS // 2
    )
    print(f"corpus generated in {time.perf_counter() - generated:.1f}s")

    new, dup, elapsed, _ = _import_subprocess(archive, tmp_path / "data")
    assert new == _CHATS * expected_message_count(_PER_CHAT)
    assert dup == 0
    assert elapsed < _INGEST_BUDGET_S, (
        f"100k WhatsApp ingest took {elapsed:.0f}s (budget {_INGEST_BUDGET_S}s)"
    )


@pytest.mark.bench
def test_budget_chrome_200k_ingest(tmp_path: Path) -> None:
    """#145's acceptance criterion: 200k visits < 2 min, with the peak-RSS
    gate pinning the memory-bounded parse (module docstring)."""
    generated = time.perf_counter()
    archive = write_chrome_takeout(tmp_path / "gen", _CHROME_COUNT, seed=42)
    print(f"corpus generated in {time.perf_counter() - generated:.1f}s")

    new, dup, elapsed, rss_kb = _import_subprocess(archive, tmp_path / "data")
    assert new == expected_visit_count(_CHROME_COUNT)
    assert dup == 0
    assert elapsed < _INGEST_BUDGET_S, (
        f"200k Chrome ingest took {elapsed:.0f}s (budget {_INGEST_BUDGET_S}s)"
    )
    assert rss_kb < _CHROME_RSS_BUDGET_KB, (
        f"peak RSS {rss_kb / 1024:.0f} MB (budget {_CHROME_RSS_BUDGET_KB / 1024:.0f} MB)"
    )
