"""Tests for Google Chat message ingestion."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from potluck.models.base import SourceType
from potluck.models.messages import ChatMessage, ChatThread, ThreadType
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.ingestion.google_takeout.chat import (
    _parse_chat_timestamp,
    ingest_chat_messages,
)


class TestChatIngestion:
    """Tests for Google Chat ingestion."""

    def test_ingest_chat_from_fixtures(self, google_takeout_fixtures_path: Path) -> None:
        """Ingest chat messages from fixture files."""
        entities = list(ingest_chat_messages(google_takeout_fixtures_path))

        # Separate threads and messages
        threads = [e for e in entities if isinstance(e, ChatThread)]
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        # Should have 2 threads (DM and Space)
        assert len(threads) == 2

        # Should have 7 messages total (4 in DM + 3 in Space)
        assert len(messages) == 7

    def test_dm_thread_properties(self, google_takeout_fixtures_path: Path) -> None:
        """DM threads have correct type and no name."""
        entities = list(ingest_chat_messages(google_takeout_fixtures_path))
        threads = [e for e in entities if isinstance(e, ChatThread)]

        # Find the DM thread
        dm_thread = next((t for t in threads if "DM" in (t.source_id or "")), None)
        assert dm_thread is not None
        assert dm_thread.thread_type == ThreadType.DIRECT
        assert dm_thread.name is None
        assert dm_thread.participant_count == 2

    def test_space_thread_properties(self, google_takeout_fixtures_path: Path) -> None:
        """Space threads have correct type and name."""
        entities = list(ingest_chat_messages(google_takeout_fixtures_path))
        threads = [e for e in entities if isinstance(e, ChatThread)]

        # Find the Space thread
        space_thread = next((t for t in threads if "Space" in (t.source_id or "")), None)
        assert space_thread is not None
        assert space_thread.thread_type == ThreadType.GROUP
        assert space_thread.name == "Tech Team"
        assert space_thread.participant_count == 3

    def test_message_content(self, google_takeout_fixtures_path: Path) -> None:
        """Messages have correct content and sender info."""
        entities = list(ingest_chat_messages(google_takeout_fixtures_path))
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        # Find a specific message
        hey_msg = next((m for m in messages if "How's it going" in (m.content or "")), None)
        assert hey_msg is not None
        assert hey_msg.sender_name == "John Doe"
        assert hey_msg.content == "Hey! How's it going?"
        assert hey_msg.source_type == SourceType.GOOGLE_TAKEOUT

    def test_message_timestamps(self, google_takeout_fixtures_path: Path) -> None:
        """Messages have correctly parsed timestamps."""
        entities = list(ingest_chat_messages(google_takeout_fixtures_path))
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        # Find message from Jan 1, 2024
        hey_msg = next((m for m in messages if "How's it going" in (m.content or "")), None)
        assert hey_msg is not None
        assert hey_msg.occurred_at is not None
        assert hey_msg.occurred_at.year == 2024
        assert hey_msg.occurred_at.month == 1
        assert hey_msg.occurred_at.day == 1
        assert hey_msg.occurred_at.hour == 10

    def test_attachment_only_message(self, google_takeout_fixtures_path: Path) -> None:
        """Messages with only attachments create placeholder text."""
        entities = list(ingest_chat_messages(google_takeout_fixtures_path))
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        # Find attachment message
        attach_msg = next((m for m in messages if "attachment" in (m.content or "")), None)
        assert attach_msg is not None
        assert attach_msg.content == "[1 attachment(s)]"

    def test_date_filter_since(self, google_takeout_fixtures_path: Path) -> None:
        """Date filter 'since' excludes earlier messages."""
        filters = PipelineFilter(since=datetime(2024, 1, 2, tzinfo=UTC))
        entities = list(ingest_chat_messages(google_takeout_fixtures_path, filters))
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        # Should exclude messages from Jan 1 and Dec 31 2023
        # Remaining: 1 from Jan 2 (DM) + 3 from Jan 3 (Space) = 4
        assert len(messages) == 4

        # Verify all are from Jan 2 or later
        for msg in messages:
            assert msg.occurred_at is not None
            assert msg.occurred_at >= filters.since  # type: ignore[operator]

    def test_date_filter_until(self, google_takeout_fixtures_path: Path) -> None:
        """Date filter 'until' excludes later messages."""
        filters = PipelineFilter(until=datetime(2024, 1, 2, tzinfo=UTC))
        entities = list(ingest_chat_messages(google_takeout_fixtures_path, filters))
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        # Should include only Jan 1 and Dec 31 2023 messages
        # That's 3 messages (2 on Jan 1 from DM + 1 on Dec 31)
        assert len(messages) == 3

    def test_content_hash_uniqueness(self, google_takeout_fixtures_path: Path) -> None:
        """Each message has a unique content hash."""
        entities = list(ingest_chat_messages(google_takeout_fixtures_path))
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        hashes = [m.content_hash for m in messages]
        assert len(hashes) == len(set(hashes))  # All unique

    def test_sender_email_in_content_json(self, google_takeout_fixtures_path: Path) -> None:
        """Sender email is stored in content_json for linking."""
        entities = list(ingest_chat_messages(google_takeout_fixtures_path))
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        # Find message with email
        msg_with_email = next(
            (m for m in messages if m.content_json and "john@example.com" in m.content_json), None
        )
        assert msg_with_email is not None

    def test_empty_directory(self) -> None:
        """Empty directory yields no entities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entities = list(ingest_chat_messages(Path(tmpdir)))
            assert entities == []


class TestParseChatTimestamp:
    """Tests for Google Chat timestamp parsing."""

    def test_parse_full_format(self) -> None:
        """Parse full Google Chat timestamp format."""
        ts = "Thursday, March 14, 2024 at 10:30:15 AM UTC"
        result = _parse_chat_timestamp(ts)
        assert result is not None
        assert result.year == 2024
        assert result.month == 3
        assert result.day == 14
        assert result.hour == 10
        assert result.minute == 30

    def test_parse_pm_time(self) -> None:
        """Parse timestamp with PM time."""
        ts = "Wednesday, January 3, 2024 at 02:00:00 PM UTC"
        result = _parse_chat_timestamp(ts)
        assert result is not None
        assert result.hour == 14  # 2 PM = 14:00

    def test_parse_midnight(self) -> None:
        """Parse timestamp near midnight."""
        ts = "Sunday, December 31, 2023 at 11:59:00 PM UTC"
        result = _parse_chat_timestamp(ts)
        assert result is not None
        assert result.year == 2023
        assert result.month == 12
        assert result.day == 31
        assert result.hour == 23
        assert result.minute == 59

    def test_parse_none(self) -> None:
        """Returns None for None input."""
        assert _parse_chat_timestamp(None) is None

    def test_parse_empty(self) -> None:
        """Returns None for empty string."""
        assert _parse_chat_timestamp("") is None


class TestChatEdgeCases:
    """Tests for chat message edge cases."""

    def test_message_without_creator(self) -> None:
        """Messages without creator info are handled gracefully."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            chat_dir = Path(tmpdir) / "Google Chat" / "Groups" / "DM test"
            chat_dir.mkdir(parents=True)

            # Message without creator field
            messages_json = {
                "messages": [
                    {
                        "text": "Message without creator",
                        "created_date": "Monday, January 1, 2024 at 10:00:00 AM UTC",
                    }
                ]
            }
            (chat_dir / "messages.json").write_text(json.dumps(messages_json))

            entities = list(ingest_chat_messages(Path(tmpdir)))
            messages = [e for e in entities if isinstance(e, ChatMessage)]

            assert len(messages) == 1
            assert messages[0].sender_name is None
            assert messages[0].content == "Message without creator"

    def test_message_with_empty_creator(self) -> None:
        """Messages with empty creator dict are handled."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            chat_dir = Path(tmpdir) / "Google Chat" / "Groups" / "DM test"
            chat_dir.mkdir(parents=True)

            messages_json = {
                "messages": [
                    {
                        "text": "Test message",
                        "creator": {},  # Empty creator
                        "created_date": "Monday, January 1, 2024 at 10:00:00 AM UTC",
                    }
                ]
            }
            (chat_dir / "messages.json").write_text(json.dumps(messages_json))

            entities = list(ingest_chat_messages(Path(tmpdir)))
            messages = [e for e in entities if isinstance(e, ChatMessage)]

            assert len(messages) == 1
            assert messages[0].sender_name is None

    def test_empty_message_skipped(self) -> None:
        """Messages with no text and no attachments are skipped."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            chat_dir = Path(tmpdir) / "Google Chat" / "Groups" / "DM test"
            chat_dir.mkdir(parents=True)

            messages_json = {
                "messages": [
                    {
                        "text": "",  # Empty text
                        "creator": {"name": "Test User"},
                        "created_date": "Monday, January 1, 2024 at 10:00:00 AM UTC",
                    },
                    {
                        "text": "Valid message",
                        "creator": {"name": "Test User"},
                        "created_date": "Monday, January 1, 2024 at 10:01:00 AM UTC",
                    },
                ]
            }
            (chat_dir / "messages.json").write_text(json.dumps(messages_json))

            entities = list(ingest_chat_messages(Path(tmpdir)))
            messages = [e for e in entities if isinstance(e, ChatMessage)]

            # Only valid message should be yielded
            assert len(messages) == 1
            assert messages[0].content == "Valid message"

    def test_message_with_invalid_timestamp(self) -> None:
        """Messages with invalid timestamps still yield with None occurred_at."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            chat_dir = Path(tmpdir) / "Google Chat" / "Groups" / "DM test"
            chat_dir.mkdir(parents=True)

            messages_json = {
                "messages": [
                    {
                        "text": "Message with bad timestamp",
                        "creator": {"name": "Test User"},
                        "created_date": "Not a valid date",
                    }
                ]
            }
            (chat_dir / "messages.json").write_text(json.dumps(messages_json))

            entities = list(ingest_chat_messages(Path(tmpdir)))
            messages = [e for e in entities if isinstance(e, ChatMessage)]

            assert len(messages) == 1
            assert messages[0].occurred_at is None

    def test_content_hash_collision_handling(self) -> None:
        """Identical messages in different threads have same content hash."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create two chat groups with same message content
            for group in ["DM group1", "DM group2"]:
                chat_dir = Path(tmpdir) / "Google Chat" / "Groups" / group
                chat_dir.mkdir(parents=True)

                messages_json = {
                    "messages": [
                        {
                            "text": "Same message text",
                            "creator": {"name": "Same User", "email": "same@example.com"},
                            "created_date": "Monday, January 1, 2024 at 10:00:00 AM UTC",
                        }
                    ]
                }
                (chat_dir / "messages.json").write_text(json.dumps(messages_json))

            entities = list(ingest_chat_messages(Path(tmpdir)))
            messages = [e for e in entities if isinstance(e, ChatMessage)]

            # Both messages have same content, sender, and timestamp
            # Content hash should be identical
            assert len(messages) == 2
            assert messages[0].content_hash == messages[1].content_hash


class TestIntegrationWithStage:
    """Integration tests with GoogleTakeoutStage."""

    def test_stage_executes_chat_ingestion(self, google_takeout_fixtures_path: Path) -> None:
        """Stage correctly routes to chat ingestion."""
        from potluck.models.base import EntityType
        from potluck.pipeline.ingestion.google_takeout import GoogleTakeoutStage

        stage = GoogleTakeoutStage()

        # Execute for chat messages only
        entities = list(
            stage.execute(
                google_takeout_fixtures_path,
                entity_types={EntityType.CHAT_MESSAGE},
            )
        )

        # Should get threads and messages
        threads = [e for e in entities if isinstance(e, ChatThread)]
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        assert len(threads) == 2
        assert len(messages) == 7
