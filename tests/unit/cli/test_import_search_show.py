"""CLI tests for import/search/show/status/dev commands."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from potluck.cli.app import app
from potluck.testing.archives import write_archive
from potluck.testing.keep import write_keep_takeout

# Deterministic corpus parameters matching spec golden count
_SEED = 7
_COUNT = 12
_GOLDEN_NEW = 11  # seed=7 / count=12 golden: 1 empty note skipped by parser
_KNOWN_WORD = "ember"  # appears in note 0 text and note 1 title "Ember Walnut Hazel"

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_keep(tmp_path: Path) -> str:
    """Build and import a Keep zip; return the archive path string."""
    zip_path = write_keep_takeout(tmp_path / "keep", _COUNT, seed=_SEED, fmt="zip")
    result = runner.invoke(app, ["import", str(zip_path)])
    assert result.exit_code == 0, f"import failed: {result.output}"
    return str(zip_path)


# ---------------------------------------------------------------------------
# import command
# ---------------------------------------------------------------------------


def test_import_command(tmp_path: Path) -> None:
    """Successful import prints source name and item counts."""
    zip_path = write_keep_takeout(tmp_path / "keep", _COUNT, seed=_SEED, fmt="zip")
    result = runner.invoke(app, ["import", str(zip_path)])
    assert result.exit_code == 0, result.output
    assert "google_keep" in result.output
    assert str(_GOLDEN_NEW) in result.output  # items_new shown

    # A status call in the same isolated env shows >= 1 import
    status_result = runner.invoke(app, ["status"])
    assert status_result.exit_code == 0
    assert "google_keep" in status_result.output


def test_import_json(tmp_path: Path) -> None:
    """--json flag produces parseable JSON with correct golden counts."""
    zip_path = write_keep_takeout(tmp_path / "keep", _COUNT, seed=_SEED, fmt="zip")
    result = runner.invoke(app, ["import", str(zip_path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["items_new"] == _GOLDEN_NEW
    assert data["status"] == "completed"
    assert data["source"] == "google_keep"


def test_import_unknown_archive(tmp_path: Path) -> None:
    """A zip with no matching members exits 1 and reports an error."""
    # No Takeout/Keep/*.json files → google_keep plugin won't detect it
    zip_path = write_archive(
        tmp_path / "unrecognised.zip",
        {"some/random/file.xyz": b"data"},
        fmt="zip",
    )
    result = runner.invoke(app, ["import", str(zip_path)])
    assert result.exit_code == 1
    # Error message should appear in combined output
    assert "no source" in result.output.lower() or "unknown" in result.output.lower()


def test_import_unsupported_path(tmp_path: Path) -> None:
    """A plain .txt path exits 1 (not a supported archive format)."""
    txt = tmp_path / "notes.txt"
    txt.write_text("not an archive")
    result = runner.invoke(app, ["import", str(txt)])
    assert result.exit_code == 1


def test_import_corrupt_zip_reports_error(tmp_path: Path) -> None:
    """A truncated/corrupt zip prints 'Error: …' and exits 1 — no raw traceback."""
    bad = tmp_path / "corrupt.zip"
    bad.write_bytes(b"PK\x03\x04" + b"\x00" * 32)  # zip magic, garbage body
    result = runner.invoke(app, ["import", str(bad)])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Traceback" not in result.output


def test_import_corrupt_tgz_reports_error(tmp_path: Path) -> None:
    bad = tmp_path / "corrupt.tgz"
    bad.write_bytes(b"\x1f\x8b" + b"\x00" * 32)  # gzip magic, garbage body
    result = runner.invoke(app, ["import", str(bad)])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Traceback" not in result.output


def test_search_limit_out_of_range_reports_error() -> None:
    """The help text advertises 1-100; out-of-range exits 1 with 'Error: …',
    not a pydantic traceback."""
    result = runner.invoke(app, ["search", "foo", "--limit", "200"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# search command
# ---------------------------------------------------------------------------


def test_search_command(tmp_path: Path) -> None:
    """Search after import returns a table with >= 1 row."""
    _import_keep(tmp_path)
    result = runner.invoke(app, ["search", _KNOWN_WORD])
    assert result.exit_code == 0, result.output
    # Table should have content rows (not just headers)
    assert _KNOWN_WORD.lower() in result.output.lower()


def test_search_json(tmp_path: Path) -> None:
    """--json produces a SearchResponse with expected keys in hits."""
    _import_keep(tmp_path)
    result = runner.invoke(app, ["search", _KNOWN_WORD, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "hits" in data
    assert len(data["hits"]) >= 1
    hit = data["hits"][0]
    for key in ("id", "kind", "title", "snippet", "score", "ts"):
        assert key in hit, f"missing key {key!r} in hit"


def test_search_no_results(tmp_path: Path) -> None:
    """A query matching nothing exits 0 with a no-results message."""
    _import_keep(tmp_path)
    result = runner.invoke(app, ["search", "zzzqqq"])
    assert result.exit_code == 0, result.output
    assert "no result" in result.output.lower()


def test_search_kind_filter(tmp_path: Path) -> None:
    """--kind email returns no results; --kind note returns results for known word."""
    _import_keep(tmp_path)

    email_result = runner.invoke(app, ["search", _KNOWN_WORD, "--kind", "email"])
    assert email_result.exit_code == 0
    # Should be empty (all keep items are notes)
    email_data = json.loads(
        runner.invoke(app, ["search", _KNOWN_WORD, "--kind", "email", "--json"]).output
    )
    assert len(email_data["hits"]) == 0

    note_data = json.loads(
        runner.invoke(app, ["search", _KNOWN_WORD, "--kind", "note", "--json"]).output
    )
    assert len(note_data["hits"]) >= 1


def test_search_invalid_kind(tmp_path: Path) -> None:
    """An invalid --kind value exits non-zero (typer enum validation)."""
    _import_keep(tmp_path)
    result = runner.invoke(app, ["search", _KNOWN_WORD, "--kind", "bogus"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# show command
# ---------------------------------------------------------------------------


def test_show_command(tmp_path: Path) -> None:
    """show displays full item details; --json produces parseable Item."""
    _import_keep(tmp_path)

    # Get an id from search
    search_result = runner.invoke(app, ["search", _KNOWN_WORD, "--json"])
    assert search_result.exit_code == 0
    hits = json.loads(search_result.output)["hits"]
    assert hits, "expected at least one hit for known word"
    item_id = str(hits[0]["id"])

    # Plain show
    show_result = runner.invoke(app, ["show", item_id])
    assert show_result.exit_code == 0, show_result.output
    # Should contain id and source
    assert item_id in show_result.output
    assert "google_keep" in show_result.output

    # JSON show
    json_result = runner.invoke(app, ["show", item_id, "--json"])
    assert json_result.exit_code == 0, json_result.output
    item_data = json.loads(json_result.output)
    assert item_data["id"] == int(item_id)
    assert isinstance(item_data["meta"], dict)
    assert item_data["source"] == "google_keep"


def test_show_missing(tmp_path: Path) -> None:
    """show with non-existent id exits 1."""
    result = runner.invoke(app, ["show", "999999"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# status command (extended)
# ---------------------------------------------------------------------------


def test_status_shows_imports(tmp_path: Path) -> None:
    """After import, status lists google_keep in the imports table."""
    _import_keep(tmp_path)

    plain_result = runner.invoke(app, ["status"])
    assert plain_result.exit_code == 0
    assert "google_keep" in plain_result.output

    json_result = runner.invoke(app, ["status", "--json"])
    assert json_result.exit_code == 0
    data = json.loads(json_result.output)
    assert "stats" in data
    assert "imports" in data
    assert len(data["imports"]) >= 1
    assert data["imports"][0]["source"] == "google_keep"
    # started_at must be ISO-8601 (contains 'T' separator), not a repr() string
    assert "T" in data["imports"][0]["started_at"]


# ---------------------------------------------------------------------------
# dev sub-app
# ---------------------------------------------------------------------------


def test_dev_new_source_nonexistent_dir(tmp_path: Path) -> None:
    """--dir pointing to a nonexistent directory exits 1 with an error message."""
    nonexistent = tmp_path / "does_not_exist" / "nested"
    result = runner.invoke(app, ["dev", "new-source", "toy_source", "--dir", str(nonexistent)])
    assert result.exit_code == 1
    assert "Error:" in result.output
    # No unhandled exception: exception is None or a clean SystemExit
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_dev_new_source_and_check(tmp_path: Path) -> None:
    """new-source scaffolds a file; check-source validates known/unknown sources."""
    # new-source: write into tmp_path to avoid polluting the real sources package
    result = runner.invoke(app, ["dev", "new-source", "toy_cli_source", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    expected_path = tmp_path / "toy_cli_source.py"
    assert expected_path.exists(), "scaffold file was not created"
    assert str(expected_path) in result.output or "toy_cli_source" in result.output

    # Second call → FileExistsError → exit 1
    dup_result = runner.invoke(app, ["dev", "new-source", "toy_cli_source", "--dir", str(tmp_path)])
    assert dup_result.exit_code == 1

    # check-source for a real registered plugin → OK
    ok_result = runner.invoke(app, ["dev", "check-source", "google_keep"])
    assert ok_result.exit_code == 0
    assert "ok" in ok_result.output.lower()

    # check-source for a nonexistent plugin → exit 1 with problems
    bad_result = runner.invoke(app, ["dev", "check-source", "nonexistent_source_xyz"])
    assert bad_result.exit_code == 1
    assert len(bad_result.output.strip()) > 0  # problems printed
