"""Tests for CLI entry point."""

import subprocess
import sys


def test_cli_shows_help_without_command() -> None:
    """CLI shows usage when run without a command."""
    result = subprocess.run(
        [sys.executable, "-m", "potluck"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Usage: potluck <command>" in result.stdout
    assert "mcp" in result.stdout
    assert "web" in result.stdout


def test_cli_unknown_command() -> None:
    """CLI exits with error for unknown command."""
    result = subprocess.run(
        [sys.executable, "-m", "potluck", "unknown"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Unknown command: unknown" in result.stdout
