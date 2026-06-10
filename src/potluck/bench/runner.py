"""Run benchmark scenarios and produce a BenchReport."""

import os
import platform
import resource
import statistics
import tempfile
import time
from pathlib import Path

from potluck.bench.registry import Tier, scenarios_for
from potluck.bench.report import BenchReport, ScenarioResult
from potluck.bench.scenarios import ALL_SCENARIOS

REPS = 5


def _fingerprint() -> dict[str, str]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": str(os.cpu_count()),
    }


def run_tier(tier: Tier, json_out: Path | None = None) -> BenchReport:
    """Run every scenario in ``tier`` REPS times; optionally write JSON."""
    results: list[ScenarioResult] = []
    for scenario in scenarios_for(tier, ALL_SCENARIOS):
        times: list[float] = []
        for _ in range(REPS):
            with tempfile.TemporaryDirectory(prefix=f"potluck-bench-{scenario.name}-") as tmp:
                workdir = Path(tmp)
                if scenario.setup is not None:
                    scenario.setup(workdir)
                start = time.perf_counter()
                scenario.run(workdir)
                times.append(time.perf_counter() - start)
        median = statistics.median(times)
        p95 = (
            statistics.quantiles(times, n=100, method="inclusive")[94]
            if len(times) > 1
            else times[0]
        )
        results.append(
            ScenarioResult(
                name=scenario.name,
                reps=REPS,
                median_s=round(median, 6),
                p95_s=round(p95, 6),
                throughput_items_s=round(scenario.item_count / median, 2) if median else 0.0,
                # Process-wide high-water mark (monotonic across scenarios);
                # per-scenario subprocess isolation arrives with RSS budgets (P2).
                peak_rss_kb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            )
        )
    report = BenchReport(tier=tier, fingerprint=_fingerprint(), results=results)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(report.model_dump_json(indent=2) + "\n")
    return report
