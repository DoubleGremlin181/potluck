"""P4 hard budgets (#142), nightly tier (@pytest.mark.bench).

Budget (4-core CPU CI-class): 100k-message WhatsApp export cold ingest
< 120 s. The import runs in a SUBPROCESS (pattern from test_p2_budgets.py):
a fresh interpreter pays the honest cost — start, imports, DB creation,
archive hash — and its measurement is isolated from the pytest process.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from potluck.testing.whatsapp import expected_message_count, write_whatsapp_export

_PER_CHAT = 25_000
_CHATS = 4
_INGEST_BUDGET_S = 120

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
[run] = runs
print(f"{run.items_new} {run.items_duplicate} {elapsed:.2f}")
"""


def _import_subprocess(archive: Path, data_home: Path) -> tuple[int, int, float]:
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
    new, dup, elapsed = proc.stdout.split()
    return int(new), int(dup), float(elapsed)


@pytest.mark.bench
def test_budget_whatsapp_100k_ingest(tmp_path: Path) -> None:
    generated = time.perf_counter()
    archive = write_whatsapp_export(
        tmp_path / "gen", _PER_CHAT, seed=42, locales=("us", "eu"), chats_per_locale=_CHATS // 2
    )
    print(f"corpus generated in {time.perf_counter() - generated:.1f}s")

    new, dup, elapsed = _import_subprocess(archive, tmp_path / "data")
    assert new == _CHATS * expected_message_count(_PER_CHAT)
    assert dup == 0
    assert elapsed < _INGEST_BUDGET_S, (
        f"100k WhatsApp ingest took {elapsed:.0f}s (budget {_INGEST_BUDGET_S}s)"
    )
