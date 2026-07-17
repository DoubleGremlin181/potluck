"""#148's RSS gate: the 93 MB Timeline.json parse must stream, never tree.

Unit tier (no bench mark — the issue's acceptance criterion is a hard
correctness property of the parser, not a perf budget): generates a
~56 MB / 150k-segment bare Timeline.json in tmp, parses it in a SUBPROCESS,
and asserts peak RSS stays far below what a whole-tree ``json.load`` costs
(v0 loaded the tree; this gate exists to kill that).

Peak RSS is read from the child's own VmHWM in /proc/self/status (Linux —
dev + CI platform), not ru_maxrss: a fork/vfork child's ru_maxrss is floored
at the spawning pytest process's resident set as of the spawn, while execve
starts the VmHWM of the fresh mm honestly (mechanism documented in
tests/unit/bench/test_p2_mbox_rss.py).

Measured on this corpus (2026-07, 4-core dev): streaming parse 198 MiB peak
(the decoded text + its read bytes + one segment; two passes keep the MAX,
not the sum) vs 272 MiB for a bare ``json.load`` of the same file — and a
regressed parser would hold tree AND text simultaneously, landing well
above 300 MiB. The 240 MiB lid sits ~20% above the streaming measurement
and below every tree-shaped failure mode.

The driver is parse-only (the mbox-gate posture, not the chrome nightly's
full import): a DB import adds ~256 MB of bulk-import page cache that would
drown the parser's memory signal, and the nightly tier owns end-to-end
budgets. Parsing a BARE file also exercises the SingleFileArchive seam this
issue added.
"""

import subprocess
import sys
import time
from pathlib import Path

from potluck.testing.timeline import (
    expected_route_count,
    expected_visit_count,
    timeline_json_bytes,
)

_SEGMENTS = 150_000
_MAX_RSS_KB = 240 * 1024
_PARSE_BUDGET_S = 60  # sanity bound; measured 2.3 s on a 4-core dev machine

_DRIVER = """
import sys, time
from pathlib import Path
from potluck.ingest.plugins import ParseContext
from potluck.ingest.readers import open_archive
from potluck.ingest.sources.timeline import parse

started = time.perf_counter()
count = sum(1 for _ in parse(open_archive(Path(sys.argv[1])), ParseContext()))
elapsed = time.perf_counter() - started

# VmHWM (KB), not ru_maxrss — see module docstring: a child's ru_maxrss is
# floored at the spawning process's resident set as of fork/vfork.
with open("/proc/self/status") as f:
    rss_kb = next(int(line.split()[1]) for line in f if line.startswith("VmHWM:"))
print(f"{count} {elapsed:.2f} {rss_kb}")
"""


def test_timeline_parse_streams_flat_memory(tmp_path: Path) -> None:
    generated = time.perf_counter()
    # The bare file must carry the real export's exact basename — detection
    # and the root glob alternative anchor on it.
    timeline = tmp_path / "Timeline.json"
    timeline.write_bytes(timeline_json_bytes(_SEGMENTS, seed=42))
    print(f"corpus generated in {time.perf_counter() - generated:.1f}s")

    proc = subprocess.run(
        [sys.executable, "-c", _DRIVER, str(timeline)],
        capture_output=True,
        text=True,
        check=True,
        timeout=_PARSE_BUDGET_S * 2,
    )
    count, elapsed, rss_kb = proc.stdout.split()
    assert int(count) == expected_visit_count(_SEGMENTS) + expected_route_count(_SEGMENTS)
    assert float(elapsed) < _PARSE_BUDGET_S, (
        f"{_SEGMENTS} segments parsed in {float(elapsed):.0f}s (budget {_PARSE_BUDGET_S}s)"
    )
    assert int(rss_kb) < _MAX_RSS_KB, (
        f"peak RSS {int(rss_kb) / 1024:.0f} MiB (lid {_MAX_RSS_KB / 1024:.0f} MiB) on a "
        f"{timeline.stat().st_size / 1e6:.0f} MB Timeline.json — parsing is not streaming"
    )
