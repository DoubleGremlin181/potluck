"""Tests for CLI entry point."""

import os

# Disable Typer's terminal detection to prevent Rich ANSI codes in CI
# Must be set BEFORE importing typer/app
os.environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"

from typer.testing import CliRunner

from potluck.core.cli import app

runner = CliRunner()


def test_cli_shows_help_without_command() -> None:
    """CLI shows usage when run without a command."""
    result = runner.invoke(app, [])
    # Typer with no_args_is_help=True shows help and exits with code 2
    assert result.exit_code == 2
    assert "Usage:" in result.output
    assert "mcp" in result.output
    assert "web" in result.output


def test_cli_shows_help_with_flag() -> None:
    """CLI shows help with --help flag."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Personal Knowledge Database" in result.output
    assert "mcp" in result.output
    assert "web" in result.output


def test_cli_unknown_command() -> None:
    """CLI exits with error for unknown command."""
    result = runner.invoke(app, ["unknown"])
    assert result.exit_code == 2
    assert "No such command 'unknown'" in result.output


def test_mcp_command_exists() -> None:
    """MCP command shows help."""
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "MCP server" in result.output


def test_web_command_exists() -> None:
    """Web command shows help with options."""
    result = runner.invoke(app, ["web", "--help"])
    assert result.exit_code == 0
    assert "--host" in result.output
    assert "--port" in result.output
