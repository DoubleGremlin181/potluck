"""Compare a bench run against a baseline; regressions gate CI."""

from pathlib import Path

from pydantic import BaseModel

from potluck.bench.report import BenchReport


class Regression(BaseModel):
    scenario: str
    metric: str
    baseline: float
    current: float
    change_pct: float


def load_report(path: Path) -> BenchReport:
    return BenchReport.model_validate_json(path.read_text())


def compare(
    baseline: BenchReport,
    current: BenchReport,
    tolerance_pct: float,
    *,
    out_of_tier: frozenset[str] = frozenset(),
    min_delta_s: float = 0.0,
) -> list[Regression]:
    """Median-time regressions beyond ``tolerance_pct``, and vanished scenarios.

    Scenarios new in ``current`` (no baseline entry yet) are not failures —
    they get a baseline on the next refresh. Baseline scenarios named in
    ``out_of_tier`` are skipped when absent from ``current``: one full-tier
    baseline file serves both gates, and a smoke run must not be penalized
    for full-only scenarios it never executes (the CLI derives this set from
    the scenario registry).

    ``min_delta_s``: a regression must ALSO exceed this absolute wall-clock
    delta. Sub-second scenarios have percentage bands at or below shared-
    runner jitter (#209: measured same-code spreads of 25-47% around the
    pooled median; a 30% band on a 0.17s scenario is 51 ms — under observed
    noise), so the gates pair a percentage with a floor. 0.0 preserves the
    pure-percentage behavior.
    """
    regressions: list[Regression] = []
    current_by_name = {result.name: result for result in current.results}
    for base in baseline.results:
        result = current_by_name.get(base.name)
        if result is None:
            if base.name in out_of_tier:
                continue
            regressions.append(
                Regression(
                    scenario=base.name,
                    metric="missing",
                    baseline=base.median_s,
                    current=float("nan"),
                    change_pct=float("inf"),
                )
            )
            continue
        change_pct = (result.median_s - base.median_s) / base.median_s * 100
        if change_pct > tolerance_pct and (result.median_s - base.median_s) > min_delta_s:
            regressions.append(
                Regression(
                    scenario=base.name,
                    metric="median_s",
                    baseline=base.median_s,
                    current=result.median_s,
                    change_pct=round(change_pct, 1),
                )
            )
    return regressions
