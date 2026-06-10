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


def compare(baseline: BenchReport, current: BenchReport, tolerance_pct: float) -> list[Regression]:
    """Median-time regressions beyond ``tolerance_pct``, and vanished scenarios.

    Scenarios new in ``current`` (no baseline entry yet) are not failures —
    they get a baseline on the next refresh.
    """
    regressions: list[Regression] = []
    current_by_name = {result.name: result for result in current.results}
    for base in baseline.results:
        result = current_by_name.get(base.name)
        if result is None:
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
        if change_pct > tolerance_pct:
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
