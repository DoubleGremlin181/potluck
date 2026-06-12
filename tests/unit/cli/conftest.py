"""Shared golden-corpus constants and import helper for CLI tests.

One authoritative definition: seed=7 / count=12 generates exactly 1 trashed
note and 0 empty notes (verified against synthetic_keep_notes), so the parser
yields 11 drafts. tests/unit/ingest/sources/test_google_keep.py pins the same
counts for the committed fixture.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from potluck.cli.app import app
from potluck.testing.keep import write_keep_takeout

GOLDEN_SEED = 7
GOLDEN_COUNT = 12
GOLDEN_NEW = 11  # 1 trashed note skipped by the parser; 0 empty


def import_keep_golden(runner: CliRunner, tmp_path: Path) -> str:
    """Build the golden Keep zip and import it; return the archive path string."""
    zip_path = write_keep_takeout(tmp_path / "keep", GOLDEN_COUNT, seed=GOLDEN_SEED, fmt="zip")
    result = runner.invoke(app, ["import", str(zip_path)])
    assert result.exit_code == 0, f"import failed: {result.output}"
    return str(zip_path)
