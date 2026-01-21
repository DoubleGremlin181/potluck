"""Tests for GoogleTakeoutStage detection and routing."""

import tempfile
from pathlib import Path

import pytest

from potluck.models.base import EntityType, SourceType
from potluck.pipeline import detect_stage, list_stages, register
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.ingestion.google_takeout import GoogleTakeoutStage


@pytest.fixture(autouse=True)
def ensure_stage_registered() -> None:
    """Ensure GoogleTakeoutStage is registered before each test.

    This handles cases where another test cleared the registry,
    since the @register decorator only runs once at import time.
    """
    register(GoogleTakeoutStage)


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

    def test_detect_location_timeline_edits(self) -> None:
        """Detects Google Takeout Timeline Edits.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Timeline Edits.json (Google Takeout format)
            timeline_dir = Path(tmpdir) / "Takeout" / "Timeline"
            timeline_dir.mkdir(parents=True)
            (timeline_dir / "Timeline Edits.json").write_text(
                '{"timelineEdits": [' + '{"rawSignal": {}},' * 10 + "]}"
            )

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


class TestGoogleTakeoutMalformedFiles:
    """Tests for graceful handling of malformed input files."""

    def test_malformed_json_in_browser_history(self) -> None:
        """Gracefully handles malformed BrowserHistory.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chrome_dir = Path(tmpdir) / "Takeout" / "Chrome"
            chrome_dir.mkdir(parents=True)
            # Write malformed JSON
            (chrome_dir / "BrowserHistory.json").write_text("{not valid json")

            stage = GoogleTakeoutStage()
            entities = list(
                stage.execute(
                    Path(tmpdir),
                    entity_types={EntityType.BROWSING_HISTORY},
                )
            )
            # Should not crash, just return empty
            assert entities == []

    def test_malformed_ics_in_calendar(self) -> None:
        """Gracefully handles malformed ICS files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            calendar_dir = Path(tmpdir) / "Takeout" / "Calendar"
            calendar_dir.mkdir(parents=True)
            # Write malformed ICS
            (calendar_dir / "calendar.ics").write_bytes(b"\x00\x01invalid binary content")

            stage = GoogleTakeoutStage()
            entities = list(
                stage.execute(
                    Path(tmpdir),
                    entity_types={EntityType.CALENDAR_EVENT},
                )
            )
            # Should not crash, just return empty
            assert entities == []

    def test_malformed_html_in_bookmarks(self) -> None:
        """Gracefully handles malformed Bookmarks.html."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chrome_dir = Path(tmpdir) / "Takeout" / "Chrome"
            chrome_dir.mkdir(parents=True)
            # Write malformed HTML with unclosed tags
            (chrome_dir / "Bookmarks.html").write_text(
                "<!DOCTYPE NETSCAPE><DL><DT><A HREF='test'>unclosed"
            )

            stage = GoogleTakeoutStage()
            entities = list(
                stage.execute(
                    Path(tmpdir),
                    entity_types={EntityType.BOOKMARK},
                )
            )
            # Should not crash - may parse partial content
            assert isinstance(entities, list)

    def test_malformed_json_in_chat(self) -> None:
        """Gracefully handles malformed messages.json.

        Note: The ChatThread is still created from directory structure,
        but no ChatMessage entities are yielded when JSON is invalid.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            chat_dir = Path(tmpdir) / "Takeout" / "Google Chat" / "Groups" / "DM test"
            chat_dir.mkdir(parents=True)
            # Write malformed JSON
            (chat_dir / "messages.json").write_text("{corrupted")

            stage = GoogleTakeoutStage()
            entities = list(
                stage.execute(
                    Path(tmpdir),
                    entity_types={EntityType.CHAT_MESSAGE},
                )
            )
            # Should have thread but no messages (malformed JSON)
            from potluck.models.messages import ChatMessage, ChatThread

            threads = [e for e in entities if isinstance(e, ChatThread)]
            messages = [e for e in entities if isinstance(e, ChatMessage)]
            assert len(threads) == 1  # Thread created from directory
            assert len(messages) == 0  # No messages parsed


class TestGoogleTakeoutDeduplication:
    """Tests for in-memory deduplication during ingestion."""

    def test_duplicate_entities_deduplicated(self) -> None:
        """Entities with same content_hash are deduplicated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chrome_dir = Path(tmpdir) / "Takeout" / "Chrome"
            chrome_dir.mkdir(parents=True)

            # Create browsing history with duplicate entries
            history_json = {
                "Browser History": [
                    {"url": "https://example.com", "time_usec": 1705000000000000},
                    {"url": "https://example.com", "time_usec": 1705000000000000},  # duplicate
                    {"url": "https://example.com", "time_usec": 1705000001000000},  # different time
                ]
            }
            import json

            (chrome_dir / "BrowserHistory.json").write_text(json.dumps(history_json))

            stage = GoogleTakeoutStage()
            entities = list(
                stage.execute(
                    Path(tmpdir),
                    entity_types={EntityType.BROWSING_HISTORY},
                )
            )

            # Should have 2 unique entries (duplicate removed)
            assert len(entities) == 2

            # Verify content_hashes are unique
            hashes = [getattr(e, "content_hash", None) for e in entities]
            assert len(hashes) == len(set(hashes))

    def test_entities_without_content_hash_not_deduplicated(self) -> None:
        """Entities without content_hash are all yielded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chrome_dir = Path(tmpdir) / "Takeout" / "Chrome"
            chrome_dir.mkdir(parents=True)

            # Create bookmarks HTML - folders don't have content_hash
            html_content = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
            <DL><p>
                <DT><H3>Folder1</H3>
                <DT><H3>Folder2</H3>
            </DL>
            """
            (chrome_dir / "Bookmarks.html").write_text(html_content)

            stage = GoogleTakeoutStage()
            entities = list(
                stage.execute(
                    Path(tmpdir),
                    entity_types={EntityType.BOOKMARK},
                )
            )

            # Both folders should be yielded
            from potluck.models.browsing import BookmarkFolder

            folders = [e for e in entities if isinstance(e, BookmarkFolder)]
            assert len(folders) == 2
