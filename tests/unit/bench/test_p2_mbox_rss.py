"""#122 acceptance: constant-memory iteration over a ~1 GB synthetic mbox.

Nightly tier (@pytest.mark.bench): generates ~1 GB in tmp, parses it in a
SUBPROCESS (ru_maxrss is a process-lifetime high-water mark — sibling tests
would pollute an in-process measurement), and asserts peak RSS stays far below
the corpus size. Nothing large is ever committed.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from potluck.testing.mbox import write_mbox

# ~2000 messages x ~500 KB bodies ≈ 1 GB.
_COUNT = 2000
_BODY_KB = 500
_MAX_RSS_MB = 300

_DRIVER = """
import resource, sys
from potluck.ingest.mbox import iter_mbox_messages, parse_email

count = 0
with open(sys.argv[1], "rb") as f:
    for raw in iter_mbox_messages(f):
        parsed = parse_email(raw)
        count += 1

rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # KB on Linux
print(f"{count} {rss_kb}")
"""


@pytest.mark.bench
def test_mbox_parse_constant_memory(tmp_path: Path) -> None:
    mbox = write_mbox(tmp_path / "big.mbox", _COUNT, seed=42, body_kb=_BODY_KB)
    assert mbox.stat().st_size > 900 * 1024 * 1024, "corpus should be ~1 GB"

    proc = subprocess.run(
        [sys.executable, "-c", _DRIVER, str(mbox)],
        capture_output=True,
        text=True,
        check=True,
        timeout=600,
    )
    count, rss_kb = (int(x) for x in proc.stdout.split())
    assert count == _COUNT
    assert rss_kb < _MAX_RSS_MB * 1024, (
        f"peak RSS {rss_kb / 1024:.0f} MB exceeds {_MAX_RSS_MB} MB on a "
        f"{mbox.stat().st_size / 1024**3:.1f} GB mbox — parsing is not streaming"
    )
