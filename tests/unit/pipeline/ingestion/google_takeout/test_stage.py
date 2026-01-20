"""Tests for GoogleTakeoutStage detection and routing."""

import tempfile
from pathlib import Path

from potluck.models.base import EntityType, SourceType
from potluck.pipeline import detect_stage, list_stages
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.ingestion.google_takeout import GoogleTakeoutStage


class TestGoogleTakeoutStageRegistration:
    """Tests for stage registration and auto-discovery."""

    def test_stage_is_registered(self) -> None:
        """GoogleTakeoutStage is auto-registered when module is imported."""
        stages = list_stages()
        assert GoogleTakeoutStage in stages

    def test_source_type(self) -> None:
        """Stage has correct source type."""
        assert GoogleTakeoutStage.SOURCE_TYPE == SourceType.GOOGLE_TAKEOUT

    def test_supported_entity_types(self) -> None:
        """Stage supports expected entity types."""
        expected = {
            EntityType.MEDIA,
            EntityType.CHAT_MESSAGE,
            EntityType.EMAIL,
            EntityType.CALENDAR_EVENT,
            EntityType.BROWSING_HISTORY,
            EntityType.BOOKMARK,
            EntityType.LOCATION_VISIT,
        }
        assert expected == GoogleTakeoutStage.SUPPORTED_ENTITY_TYPES


class TestGoogleTakeoutStageDetection:
    """Tests for filename pattern matching."""

    def test_detect_takeout_zip(self) -> None:
        """Detects takeout ZIP files."""
        result = detect_stage(Path("takeout-20240115T123456Z-001.zip"))
        assert result is GoogleTakeoutStage

    def test_detect_takeout_tgz(self) -> None:
        """Detects takeout TGZ files."""
        result = detect_stage(Path("takeout-20240115T123456Z-001.tgz"))
        assert result is GoogleTakeoutStage

    def test_detect_takeout_tar_gz(self) -> None:
        """Detects takeout TAR.GZ files."""
        result = detect_stage(Path("takeout-20240115T123456Z-001.tar.gz"))
        assert result is GoogleTakeoutStage

    def test_detect_takeout_folder(self) -> None:
        """Detects extracted Takeout folder."""
        result = detect_stage(Path("Takeout"))
        assert result is GoogleTakeoutStage

    def test_no_match_unrelated_zip(self) -> None:
        """Does not match unrelated ZIP files."""
        result = detect_stage(Path("random-archive.zip"))
        assert result is None


class TestGoogleTakeoutStageDetectionCounts:
    """Tests for entity count detection in takeout archives."""

    def test_detect_empty_directory(self) -> None:
        """Returns empty counts for empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stage = GoogleTakeoutStage()
            result = stage.detect(Path(tmpdir))
            assert result.entity_counts == {}
            assert result.metadata == {}

    def test_detect_google_photos(self) -> None:
        """Detects Google Photos media files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Google Photos structure
            photos_dir = Path(tmpdir) / "Takeout" / "Google Photos" / "Album"
            photos_dir.mkdir(parents=True)
            (photos_dir / "photo1.jpg").write_bytes(b"fake jpeg")
            (photos_dir / "photo2.png").write_bytes(b"fake png")
            (photos_dir / "video.mp4").write_bytes(b"fake video")

            stage = GoogleTakeoutStage()
            result = stage.detect(Path(tmpdir))

            assert EntityType.MEDIA in result.entity_counts
            assert result.entity_counts[EntityType.MEDIA] == 3

    def test_detect_chrome_history(self) -> None:
        """Detects Chrome browsing history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chrome_dir = Path(tmpdir) / "Takeout" / "Chrome"
            chrome_dir.mkdir(parents=True)
            # Create a ~2KB file (should detect ~10 entries)
            (chrome_dir / "BrowserHistory.json").write_text('{"Browser History": []}' + "x" * 2000)

            stage = GoogleTakeoutStage()
            result = stage.detect(Path(tmpdir))

            assert EntityType.BROWSING_HISTORY in result.entity_counts
            assert result.entity_counts[EntityType.BROWSING_HISTORY] > 0

    def test_detect_chrome_bookmarks(self) -> None:
        """Detects Chrome bookmarks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chrome_dir = Path(tmpdir) / "Takeout" / "Chrome"
            chrome_dir.mkdir(parents=True)
            html_content = """
            <!DOCTYPE NETSCAPE-Bookmark-file-1>
            <DL><p>
                <DT><A HREF="https://example.com">Example</A>
                <DT><A HREF="https://test.com">Test</A>
            </DL>
            """
            (chrome_dir / "Bookmarks.html").write_text(html_content)

            stage = GoogleTakeoutStage()
            result = stage.detect(Path(tmpdir))

            assert EntityType.BOOKMARK in result.entity_counts
            assert result.entity_counts[EntityType.BOOKMARK] == 2

    def test_detect_calendar_events(self) -> None:
        """Detects Google Calendar events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            calendar_dir = Path(tmpdir) / "Takeout" / "Calendar"
            calendar_dir.mkdir(parents=True)
            ics_content = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Event 1
END:VEVENT
BEGIN:VEVENT
SUMMARY:Event 2
END:VEVENT
END:VCALENDAR"""
            (calendar_dir / "calendar.ics").write_text(ics_content)

            stage = GoogleTakeoutStage()
            result = stage.detect(Path(tmpdir))

            assert EntityType.CALENDAR_EVENT in result.entity_counts
            assert result.entity_counts[EntityType.CALENDAR_EVENT] == 2

    def test_detect_google_chat(self) -> None:
        """Detects Google Chat messages."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chat_dir = Path(tmpdir) / "Takeout" / "Google Chat" / "Groups" / "DM abc123"
            chat_dir.mkdir(parents=True)
            # Create a messages.json file (~1KB = ~2 messages estimate)
            (chat_dir / "messages.json").write_text('{"messages": []}' + "x" * 1000)

            stage = GoogleTakeoutStage()
            result = stage.detect(Path(tmpdir))

            assert EntityType.CHAT_MESSAGE in result.entity_counts
            assert result.entity_counts[EntityType.CHAT_MESSAGE] > 0

    def test_detect_gmail(self) -> None:
        """Detects Gmail mbox files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mail_dir = Path(tmpdir) / "Takeout" / "Mail"
            mail_dir.mkdir(parents=True)
            # Create a ~20KB file (should detect ~2 messages)
            (mail_dir / "All mail Including Spam and Trash.mbox").write_text("x" * 20000)

            stage = GoogleTakeoutStage()
            result = stage.detect(Path(tmpdir))

            assert EntityType.EMAIL in result.entity_counts
            assert result.entity_counts[EntityType.EMAIL] > 0

    def test_detect_location_timeline_json(self) -> None:
        """Detects Android Timeline export (Timeline.json)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Timeline.json at root (Android export format)
            (Path(tmpdir) / "Timeline.json").write_text('{"semanticSegments": []}' + "x" * 1000)

            stage = GoogleTakeoutStage()
            result = stage.detect(Path(tmpdir))

            assert EntityType.LOCATION_VISIT in result.entity_counts
            assert result.entity_counts[EntityType.LOCATION_VISIT] > 0

    def test_detect_multiple_entity_types(self) -> None:
        """Detects multiple entity types in same takeout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create multiple data types
            (Path(tmpdir) / "Takeout" / "Google Photos" / "Album").mkdir(parents=True)
            (Path(tmpdir) / "Takeout" / "Google Photos" / "Album" / "photo.jpg").write_bytes(b"x")

            (Path(tmpdir) / "Takeout" / "Chrome").mkdir(parents=True)
            (Path(tmpdir) / "Takeout" / "Chrome" / "Bookmarks.html").write_text('<A HREF="x">')

            stage = GoogleTakeoutStage()
            result = stage.detect(Path(tmpdir))

            assert EntityType.MEDIA in result.entity_counts
            assert EntityType.BOOKMARK in result.entity_counts
            assert "Google Takeout" in result.metadata.get("source", "")


class TestGoogleTakeoutStageExecution:
    """Tests for execute() method routing."""

    def test_execute_empty_yields_nothing(self) -> None:
        """Execute on empty directory yields no entities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stage = GoogleTakeoutStage()
            entities = list(stage.execute(Path(tmpdir)))
            assert entities == []

    def test_execute_filters_entity_types(self) -> None:
        """Execute only processes requested entity types."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stage = GoogleTakeoutStage()

            # Request only MEDIA type
            entities = list(
                stage.execute(
                    Path(tmpdir),
                    entity_types={EntityType.MEDIA},
                )
            )
            # Empty because no photos, but should not error
            assert entities == []

    def test_execute_with_filters(self) -> None:
        """Execute accepts date filters."""
        from datetime import UTC, datetime

        with tempfile.TemporaryDirectory() as tmpdir:
            stage = GoogleTakeoutStage()

            filters = PipelineFilter(
                since=datetime(2024, 1, 1, tzinfo=UTC),
                until=datetime(2024, 12, 31, tzinfo=UTC),
            )
            entities = list(
                stage.execute(
                    Path(tmpdir),
                    entity_types={EntityType.BROWSING_HISTORY},
                    filters=filters,
                )
            )
            assert entities == []


class TestGoogleTakeoutInstructions:
    """Tests for instructions loading."""

    def test_instructions_load(self) -> None:
        """Instructions file can be loaded."""
        instructions = GoogleTakeoutStage.get_instructions()
        assert "Google Takeout" in instructions
        assert "takeout.google.com" in instructions

    def test_instructions_contain_steps(self) -> None:
        """Instructions contain export steps."""
        instructions = GoogleTakeoutStage.get_instructions()
        assert "Step 1" in instructions
        assert "Google Photos" in instructions
        assert "Gmail" in instructions


class TestGoogleTakeoutDirectoryFinding:
    """Tests for directory finding helper methods."""

    def test_find_google_photos_dir_takeout_nested(self) -> None:
        """Finds Google Photos under Takeout/ prefix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            photos_dir = Path(tmpdir) / "Takeout" / "Google Photos"
            photos_dir.mkdir(parents=True)

            stage = GoogleTakeoutStage()
            result = stage._find_google_photos_dir(Path(tmpdir))
            assert result == photos_dir

    def test_find_google_photos_dir_direct(self) -> None:
        """Finds Google Photos at root level."""
        with tempfile.TemporaryDirectory() as tmpdir:
            photos_dir = Path(tmpdir) / "Google Photos"
            photos_dir.mkdir(parents=True)

            stage = GoogleTakeoutStage()
            result = stage._find_google_photos_dir(Path(tmpdir))
            assert result == photos_dir

    def test_find_google_photos_dir_missing(self) -> None:
        """Returns None when Google Photos not found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stage = GoogleTakeoutStage()
            result = stage._find_google_photos_dir(Path(tmpdir))
            assert result is None
