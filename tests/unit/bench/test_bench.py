"""Bench rig: run writes valid JSON; compare gates on regressions."""

import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from potluck.bench.registry import Scenario, scenarios_for
from potluck.bench.report import BenchReport, ScenarioResult
from potluck.bench.runner import REPS, run_tier
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


def test_compare_smoke_run_skips_full_only_baseline_scenarios(tmp_path: Path) -> None:
    """One baseline file serves both gates: a smoke-tier run is not penalized
    for full-only scenarios it never runs (e.g. ingest_keep_10k), while a
    full-tier run missing them still fails."""
    full_baseline = BenchReport(
        tier="full",
        fingerprint={"platform": "test", "python": "3.13", "cpu_count": "4"},
        results=[
            _report(0.100).results[0],
            _report(1.0, name="ingest_keep_10k").results[0],
        ],
    )
    base, current = tmp_path / "base.json", tmp_path / "current.json"
    base.write_text(full_baseline.model_dump_json())
    current.write_text(_report(0.100).model_dump_json())  # tier=smoke, 10k absent

    smoke_result = runner.invoke(
        app, ["bench", "compare", str(base), str(current), "--tolerance", "30"]
    )
    assert smoke_result.exit_code == 0, smoke_result.output

    full_current = _report(0.100)
    full_current = full_current.model_copy(update={"tier": "full"})
    current.write_text(full_current.model_dump_json())
    full_result = runner.invoke(
        app, ["bench", "compare", str(base), str(current), "--tolerance", "30"]
    )
    assert full_result.exit_code == 1
    assert "missing" in full_result.output.lower()


def test_compare_fails_when_scenario_disappears(tmp_path: Path) -> None:
    base, current = tmp_path / "base.json", tmp_path / "current.json"
    base.write_text(_report(0.100).model_dump_json())
    current.write_text(_report(0.100, name="other_scenario").model_dump_json())
    result = runner.invoke(app, ["bench", "compare", str(base), str(current)])
    assert result.exit_code == 1
    assert "missing" in result.output.lower()


def test_runner_calls_setup_before_run_and_excludes_from_timing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """setup() must be called before run() and its duration must not appear in timing."""
    call_order: list[str] = []

    def setup(workdir: Path) -> None:
        call_order.append("setup")
        # 50ms sleep that must NOT appear in the measured median
        time.sleep(0.05)

    def run(workdir: Path) -> None:
        call_order.append("run")

    test_scenario = Scenario(name="test_setup", tier="smoke", item_count=1, run=run, setup=setup)
    monkeypatch.setattr("potluck.bench.runner.ALL_SCENARIOS", [test_scenario])

    report = run_tier("smoke")

    # setup must precede run in every repetition
    assert call_order == ["setup", "run"] * REPS
    # 50ms sleep in setup must NOT inflate the measured median
    assert report.results[0].median_s < 0.05


def test_peak_rss_normalized_to_kb_per_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """getrusage().ru_maxrss is KB on Linux but BYTES on macOS: reports must
    not mix units depending on which machine produced the JSON."""
    import resource
    import sys

    from potluck.bench import runner as runner_mod

    fake = resource.struct_rusage((0.0, 0.0, 4096, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))
    monkeypatch.setattr(resource, "getrusage", lambda _who: fake)

    monkeypatch.setattr(sys, "platform", "linux")
    assert runner_mod._peak_rss_kb() == 4096

    monkeypatch.setattr(sys, "platform", "darwin")
    assert runner_mod._peak_rss_kb() == 4  # bytes → KB
