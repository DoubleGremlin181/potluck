"""CLI shell tests: every command runs and reuses the service layer."""

from typing import Any

import pytest
from fastapi import FastAPI
from typer.testing import CliRunner

from potluck import __version__
from potluck.cli.app import app

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_status_prints_stats_from_service() -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "items: 0" in result.output
    assert "sources: 0" in result.output
    assert f"version: {__version__}" in result.output


def test_serve_wires_uvicorn_with_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(app_obj: Any, **kwargs: Any) -> None:
        captured["app"] = app_obj
        captured.update(kwargs)

    monkeypatch.setattr("uvicorn.run", fake_run)
    result = runner.invoke(app, ["serve", "--no-browser", "--port", "9999"])
    assert result.exit_code == 0
    assert isinstance(captured["app"], FastAPI)
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9999


def test_mcp_command_runs_stdio_then_http(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def fake_stdio(ctx: Any) -> None:
        calls.append("stdio")

    def fake_http(ctx: Any, *, host: str, port: int) -> None:
        calls.append(("http", host, port))

    monkeypatch.setattr("potluck.cli.app.run_stdio", fake_stdio)
    monkeypatch.setattr("potluck.cli.app.run_http", fake_http)
    assert runner.invoke(app, ["mcp"]).exit_code == 0
    assert runner.invoke(app, ["mcp", "--http", "--port", "9000"]).exit_code == 0
    assert calls == ["stdio", ("http", "127.0.0.1", 9000)]


def test_bench_run_prints_summary() -> None:
    result = runner.invoke(app, ["bench", "run", "--tier", "smoke"])
    assert result.exit_code == 0
    assert "meta_roundtrip_5k" in result.output
    assert "items/s" in result.output
