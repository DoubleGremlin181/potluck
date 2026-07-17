"""CLI tests for the rm and forget commands (#153).

Destructive-operation care is part of the contract: without --yes both
commands prompt, and declining leaves the database untouched.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from potluck.cli.app import app
from tests.unit.cli.conftest import GOLDEN_NEW, import_keep_golden

runner = CliRunner()


def _query_one(sql: str) -> int:
    """Ground truth straight from the isolated test database."""
    conn = sqlite3.connect(os.environ["POTLUCK_DB_PATH"])
    try:
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# rm
# ---------------------------------------------------------------------------


def test_rm_import_with_yes_deletes_and_prints_counts(tmp_path: Path) -> None:
    import_keep_golden(runner, tmp_path)

    result = runner.invoke(app, ["rm", "--import", "1", "--yes"])

    assert result.exit_code == 0, result.output
    assert str(GOLDEN_NEW) in result.output  # deleted-items count
    assert _query_one("SELECT COUNT(*) FROM items") == 0
    assert _query_one("SELECT COUNT(*) FROM imports") == 0


def test_rm_prompts_and_declining_deletes_nothing(tmp_path: Path) -> None:
    import_keep_golden(runner, tmp_path)

    result = runner.invoke(app, ["rm", "--import", "1"], input="n\n")

    assert result.exit_code != 0
    assert _query_one("SELECT COUNT(*) FROM items") == GOLDEN_NEW
    assert _query_one("SELECT COUNT(*) FROM imports") == 1


def test_rm_prompt_accepts_confirmation(tmp_path: Path) -> None:
    import_keep_golden(runner, tmp_path)

    result = runner.invoke(app, ["rm", "--import", "1"], input="y\n")

    assert result.exit_code == 0, result.output
    assert _query_one("SELECT COUNT(*) FROM items") == 0


def test_rm_requires_exactly_one_selector(tmp_path: Path) -> None:
    no_selector = runner.invoke(app, ["rm", "--yes"])
    assert no_selector.exit_code == 2

    two_selectors = runner.invoke(app, ["rm", "--import", "1", "--source", "google_keep", "--yes"])
    assert two_selectors.exit_code == 2


def test_rm_item_ids(tmp_path: Path) -> None:
    import_keep_golden(runner, tmp_path)

    result = runner.invoke(app, ["rm", "1", "2", "--yes"])

    assert result.exit_code == 0, result.output
    assert _query_one("SELECT COUNT(*) FROM items") == GOLDEN_NEW - 2
    # Plain rm of items clears the owning run's file_hash (re-import can restore).
    assert _query_one("SELECT COUNT(*) FROM imports WHERE file_hash IS NULL") == 1


def test_rm_source(tmp_path: Path) -> None:
    import_keep_golden(runner, tmp_path)

    result = runner.invoke(app, ["rm", "--source", "google_keep", "--yes"])

    assert result.exit_code == 0, result.output
    assert _query_one("SELECT COUNT(*) FROM items") == 0
    assert _query_one("SELECT COUNT(*) FROM imports") == 0
    assert _query_one("SELECT COUNT(*) FROM sources WHERE name = 'google_keep'") == 1


def test_rm_unknown_import_errors(tmp_path: Path) -> None:
    result = runner.invoke(app, ["rm", "--import", "42", "--yes"])
    assert result.exit_code == 1
    assert "Error:" in result.output


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------


def test_forget_import_suppresses_and_blocks_reimport(tmp_path: Path) -> None:
    archive = import_keep_golden(runner, tmp_path)

    result = runner.invoke(app, ["forget", "--import", "1", "--yes"])

    assert result.exit_code == 0, result.output
    assert "suppressed" in result.output
    assert _query_one("SELECT COUNT(*) FROM suppressed_hashes") == GOLDEN_NEW

    reimport = runner.invoke(app, ["import", archive])
    assert reimport.exit_code == 0, reimport.output
    assert _query_one("SELECT COUNT(*) FROM items") == 0
    assert _query_one("SELECT items_suppressed FROM imports ORDER BY id DESC LIMIT 1") == (
        GOLDEN_NEW
    )


def test_forget_prompts_without_yes(tmp_path: Path) -> None:
    import_keep_golden(runner, tmp_path)

    result = runner.invoke(app, ["forget", "--import", "1"], input="n\n")

    assert result.exit_code != 0
    assert _query_one("SELECT COUNT(*) FROM items") == GOLDEN_NEW
    assert _query_one("SELECT COUNT(*) FROM suppressed_hashes") == 0


def test_rm_json_output(tmp_path: Path) -> None:
    import json

    import_keep_golden(runner, tmp_path)

    result = runner.invoke(app, ["rm", "--import", "1", "--yes", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["items_deleted"] == GOLDEN_NEW
    assert data["imports_deleted"] == 1
    assert data["hashes_suppressed"] == 0
