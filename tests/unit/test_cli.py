"""CLI shell tests."""

from typer.testing import CliRunner

from potluck import __version__
from potluck.cli.app import app

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__
