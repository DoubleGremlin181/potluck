"""CLI tests for the list command."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from potluck.cli.app import app
from potluck.testing.keep import write_keep_takeout

_SEED = 7
_COUNT = 12
_GOLDEN_NEW = 11  # seed=7 / count=12 golden: 1 trashed note skipped by parser

runner = CliRunner()


def _import_keep(tmp_path: Path) -> None:
    zip_path = write_keep_takeout(tmp_path / "keep", _COUNT, seed=_SEED, fmt="zip")
    result = runner.invoke(app, ["import", str(zip_path)])
    assert result.exit_code == 0, f"import failed: {result.output}"


def test_list_table_output(tmp_path: Path) -> None:
    """Default list shows a table with the imported notes."""
    _import_keep(tmp_path)
    result = runner.invoke(app, ["list", "--limit", "100"])
    assert result.exit_code == 0, result.output
    assert "google_keep" in result.output
    assert f"of {_GOLDEN_NEW}" in result.output  # footer: showing X-Y of total


def test_list_limit_out_of_range_reports_error() -> None:
    """The help text advertises 1-100; out-of-range exits 1 with 'Error: …',
    not a pydantic traceback."""
    result = runner.invoke(app, ["list", "--limit", "200"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Traceback" not in result.output


def test_list_json_matches_filters(tmp_path: Path) -> None:
    """--json output parses; kind/source filters and pagination are honoured."""
    _import_keep(tmp_path)

    result = runner.invoke(
        app,
        ["list", "--kind", "note", "--source", "google_keep", "--limit", "5", "--json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["total"] == _GOLDEN_NEW
    assert len(data["items"]) == 5
    assert all(i["kind"] == "note" for i in data["items"])
    assert all(i["source"] == "google_keep" for i in data["items"])

    empty = runner.invoke(app, ["list", "--kind", "email", "--json"])
    assert empty.exit_code == 0
    assert json.loads(empty.output)["items"] == []


def test_list_since_until_and_sort(tmp_path: Path) -> None:
    """Date-range filtering plus ts_asc sorting returns chronological items."""
    _import_keep(tmp_path)

    result = runner.invoke(
        app,
        ["list", "--since", "2020-01-01", "--sort", "ts_asc", "--limit", "100", "--json"],
    )
    assert result.exit_code == 0, result.output
    ts_values = [i["ts"] for i in json.loads(result.output)["items"] if i["ts"] is not None]
    assert ts_values == sorted(ts_values)
    assert len(ts_values) > 0


def test_list_empty_db(tmp_path: Path) -> None:
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No items found" in result.output


def test_list_invalid_sort_rejected(tmp_path: Path) -> None:
    result = runner.invoke(app, ["list", "--sort", "bogus"])
    assert result.exit_code != 0
