"""Bench rig: run writes valid JSON; compare gates on regressions."""

from pathlib import Path

from typer.testing import CliRunner

from potluck.bench.registry import scenarios_for
from potluck.bench.report import BenchReport, ScenarioResult
from potluck.bench.scenarios import ALL_SCENARIOS
from potluck.cli.app import app

runner = CliRunner()


def _report(median_s: float, name: str = "meta_roundtrip_5k") -> BenchReport:
    return BenchReport(
        tier="smoke",
        fingerprint={"platform": "test", "python": "3.13", "cpu_count": "4"},
        results=[
            ScenarioResult(
                name=name,
                reps=5,
                median_s=median_s,
                p95_s=median_s * 1.1,
                throughput_items_s=5000 / median_s,
                peak_rss_kb=100_000,
            )
        ],
    )


def test_registry_smoke_is_subset_of_full() -> None:
    smoke = {s.name for s in scenarios_for("smoke", ALL_SCENARIOS)}
    full = {s.name for s in scenarios_for("full", ALL_SCENARIOS)}
    assert smoke <= full
    assert "meta_roundtrip_5k" in smoke


def test_bench_run_smoke_writes_valid_json(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    result = runner.invoke(app, ["bench", "run", "--tier", "smoke", "--json", str(out)])
    assert result.exit_code == 0, result.output
    report = BenchReport.model_validate_json(out.read_text())
    assert report.tier == "smoke"
    assert report.results
    first = report.results[0]
    assert first.median_s > 0
    assert first.throughput_items_s > 0
    assert first.peak_rss_kb > 0
    assert report.fingerprint["python"]


def test_bench_run_rejects_unknown_tier() -> None:
    assert runner.invoke(app, ["bench", "run", "--tier", "nope"]).exit_code != 0


def test_compare_passes_within_tolerance(tmp_path: Path) -> None:
    base, current = tmp_path / "base.json", tmp_path / "current.json"
    base.write_text(_report(0.100).model_dump_json())
    current.write_text(_report(0.110).model_dump_json())  # +10% < 30%
    result = runner.invoke(app, ["bench", "compare", str(base), str(current), "--tolerance", "30"])
    assert result.exit_code == 0, result.output


def test_compare_fails_on_regression(tmp_path: Path) -> None:
    base, current = tmp_path / "base.json", tmp_path / "current.json"
    base.write_text(_report(0.100).model_dump_json())
    current.write_text(_report(0.200).model_dump_json())  # +100% > 30%
    result = runner.invoke(app, ["bench", "compare", str(base), str(current), "--tolerance", "30"])
    assert result.exit_code == 1
    assert "median_s" in result.output


def test_compare_fails_when_scenario_disappears(tmp_path: Path) -> None:
    base, current = tmp_path / "base.json", tmp_path / "current.json"
    base.write_text(_report(0.100).model_dump_json())
    current.write_text(_report(0.100, name="other_scenario").model_dump_json())
    result = runner.invoke(app, ["bench", "compare", str(base), str(current)])
    assert result.exit_code == 1
    assert "missing" in result.output.lower()
