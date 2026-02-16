"""Unit tests for CLI commands."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from potluck.core.cli import (
    _resolve_entity_types,
    _validate_entity_types,
    _validate_source_type,
    app,
)
from potluck.models.base import EntityType, SourceType

runner = CliRunner()


class TestValidateSourceType:
    """Tests for _validate_source_type helper function."""

    def test_valid_source_types(self) -> None:
        """Test that valid source types are accepted."""
        assert _validate_source_type("google_takeout") == SourceType.GOOGLE_TAKEOUT
        assert _validate_source_type("android_timeline") == SourceType.ANDROID_TIMELINE
        assert _validate_source_type("reddit") == SourceType.REDDIT
        assert _validate_source_type("generic") == SourceType.GENERIC

    def test_invalid_source_type_raises(self) -> None:
        """Test that invalid source types raise BadParameter."""
        with pytest.raises(typer.BadParameter) as exc_info:
            _validate_source_type("invalid_source")
        assert "Invalid source type" in str(exc_info.value)
        assert "google_takeout" in str(exc_info.value)  # Valid types listed


class TestValidateEntityTypes:
    """Tests for _validate_entity_types helper function."""

    def test_valid_entity_types(self) -> None:
        """Test that valid entity types are accepted."""
        available = {EntityType.MEDIA, EntityType.CALENDAR_EVENT}
        result = _validate_entity_types(["media", "calendar_event"], available)
        assert result == {EntityType.MEDIA, EntityType.CALENDAR_EVENT}

    def test_unavailable_entity_type_raises(self) -> None:
        """Test that unavailable entity types raise BadParameter."""
        available = {EntityType.MEDIA}
        with pytest.raises(typer.BadParameter) as exc_info:
            _validate_entity_types(["email"], available)
        assert "not available" in str(exc_info.value)

    def test_invalid_entity_type_raises(self) -> None:
        """Test that invalid entity types raise BadParameter."""
        available = {EntityType.MEDIA}
        with pytest.raises(typer.BadParameter) as exc_info:
            _validate_entity_types(["invalid_type"], available)
        assert "Invalid entity type" in str(exc_info.value)


class TestResolveEntityTypes:
    """Tests for _resolve_entity_types helper function."""

    def test_returns_all_when_non_interactive(self) -> None:
        """Test that all types are returned in non-interactive mode."""
        available = {EntityType.MEDIA: 100, EntityType.CALENDAR_EVENT: 50}
        result = _resolve_entity_types(available, requested=None, interactive=False)
        assert result == {EntityType.MEDIA, EntityType.CALENDAR_EVENT}

    def test_uses_requested_types(self) -> None:
        """Test that requested types are validated and returned."""
        available = {EntityType.MEDIA: 100, EntityType.CALENDAR_EVENT: 50}
        result = _resolve_entity_types(available, requested=["media"], interactive=False)
        assert result == {EntityType.MEDIA}

    def test_raises_for_invalid_requested_types(self) -> None:
        """Test that invalid requested types raise BadParameter."""
        available = {EntityType.MEDIA: 100}
        with pytest.raises(typer.BadParameter):
            _resolve_entity_types(available, requested=["invalid"], interactive=False)


class TestIngestCommand:
    """Tests for the ingest CLI command."""

    def test_ingest_help(self) -> None:
        """Test that ingest command shows help."""
        result = runner.invoke(app, ["ingest", "--help"])
        assert result.exit_code == 0
        assert "Import data and run the full processing pipeline" in result.stdout
        assert "--dry-run" in result.stdout
        assert "--source" in result.stdout
        assert "--type" in result.stdout

    def test_ingest_nonexistent_path(self) -> None:
        """Test that ingest fails with nonexistent path."""
        result = runner.invoke(app, ["ingest", "/nonexistent/path"])
        assert result.exit_code != 0
        # Typer validates exists=True

    def test_dry_run_json_output(self, tmp_path: Path) -> None:
        """Test dry run with JSON output."""
        # Create a temp file
        test_file = tmp_path / "Timeline.json"
        test_file.write_text("{}")

        # Mock at the location where it's used (top-level import in cli module)
        with (
            patch("potluck.core.cli.detect_stage") as mock_detect_stage,
            patch("potluck.core.cli.discover") as mock_discover,
        ):
            # Mock stage detection
            mock_stage = MagicMock()
            mock_stage.SOURCE_TYPE = SourceType.ANDROID_TIMELINE
            mock_detect_stage.return_value = mock_stage

            # Mock discovery result
            from potluck.pipeline import DiscoveryResult

            mock_discover.return_value = DiscoveryResult(
                source_path=test_file,
                stage=mock_stage,
                available_entities={EntityType.LOCATION_VISIT: 100},
                metadata={},
            )

            result = runner.invoke(app, ["ingest", str(test_file), "--dry-run", "--json"])
            assert result.exit_code == 0
            assert '"source_type": "android_timeline"' in result.stdout
            assert '"location_visit": 100' in result.stdout

    def test_auto_detect_fails_suggests_source(self, tmp_path: Path) -> None:
        """Test that auto-detection failure suggests --source flag."""
        test_file = tmp_path / "unknown.txt"
        test_file.write_text("test")

        with patch("potluck.core.cli.detect_stage") as mock_detect_stage:
            mock_detect_stage.return_value = None

            result = runner.invoke(app, ["ingest", str(test_file)], catch_exceptions=False)
            # Check either stdout or output (combined) for error message
            output = result.stdout + (result.output if hasattr(result, "output") else "")
            assert result.exit_code != 0
            assert "--source" in output or "--source" in str(result.exception)

    def test_invalid_source_type(self, tmp_path: Path) -> None:
        """Test that invalid --source raises error."""
        test_file = tmp_path / "test.tgz"
        test_file.write_text("test")

        result = runner.invoke(app, ["ingest", str(test_file), "--source", "invalid_source"])
        # Check output which includes both stdout and stderr
        output = result.output if hasattr(result, "output") else result.stdout
        assert result.exit_code != 0
        assert "Invalid source type" in output


class TestOtherCommands:
    """Tests for other CLI commands."""

    def test_help(self) -> None:
        """Test that app shows help."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "potluck" in result.stdout.lower()

    def test_mcp_command_exists(self) -> None:
        """Test that mcp command is available."""
        result = runner.invoke(app, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "MCP server" in result.stdout

    def test_web_command_exists(self) -> None:
        """Test that web command is available."""
        result = runner.invoke(app, ["web", "--help"])
        assert result.exit_code == 0
        assert "web ui" in result.stdout.lower()

    def test_download_models_command_exists(self) -> None:
        """Test that download-models command is available."""
        result = runner.invoke(app, ["download-models", "--help"])
        assert result.exit_code == 0
        assert "models" in result.stdout.lower()
