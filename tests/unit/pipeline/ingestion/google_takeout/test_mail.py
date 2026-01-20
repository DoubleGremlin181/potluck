"""Tests for Gmail email ingestion."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from potluck.models.base import SourceType
from potluck.models.email import Email, EmailFolder, EmailThread
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.ingestion.google_takeout.mail import (
    _labels_to_folder,
    _parse_gmail_labels,
    ingest_emails,
)

# Path to test fixtures
FIXTURES_PATH = Path(__file__).parent.parent.parent.parent.parent / "fixtures" / "google_takeout"


class TestEmailIngestion:
    """Tests for Gmail email ingestion."""

    def test_ingest_emails_from_fixtures(self) -> None:
        """Ingest emails from fixture files."""
        entities = list(ingest_emails(FIXTURES_PATH))

        # Separate entity types
        emails = [e for e in entities if isinstance(e, Email)]
        threads = [e for e in entities if isinstance(e, EmailThread)]

        # Should have 4 emails and 3 threads (2 emails share a thread)
        assert len(emails) == 4
        assert len(threads) == 3

    def test_email_basic_fields(self) -> None:
        """Email has correct basic fields."""
        entities = list(ingest_emails(FIXTURES_PATH))
        emails = [e for e in entities if isinstance(e, Email)]

        # Find the first email
        meeting_email = next(
            (e for e in emails if e.subject == "Meeting Tomorrow"),
            None,
        )
        assert meeting_email is not None
        assert meeting_email.from_address == "john.doe@example.com"
        assert meeting_email.from_name == "John Doe"
        assert meeting_email.message_id == "msg001@example.com"
        assert "jane.smith@example.com" in (meeting_email.to_addresses or "")
        assert "bob@example.com" in (meeting_email.cc_addresses or "")
        assert "Let's meet tomorrow" in (meeting_email.body_text or "")

    def test_email_threading(self) -> None:
        """Reply email has correct in_reply_to and references."""
        entities = list(ingest_emails(FIXTURES_PATH))
        emails = [e for e in entities if isinstance(e, Email)]

        # Find the reply email
        reply_email = next(
            (e for e in emails if e.subject == "Re: Meeting Tomorrow"),
            None,
        )
        assert reply_email is not None
        assert reply_email.in_reply_to == "msg001@example.com"
        assert "msg001@example.com" in (reply_email.references or "")
        # Should be linked to a thread
        assert reply_email.thread_id is not None

    def test_thread_creation(self) -> None:
        """Thread has correct metadata."""
        entities = list(ingest_emails(FIXTURES_PATH))
        threads = [e for e in entities if isinstance(e, EmailThread)]

        # Find thread by source_id
        meeting_thread = next(
            (t for t in threads if t.source_id and "1234567890123456789" in t.source_id),
            None,
        )
        assert meeting_thread is not None
        assert meeting_thread.subject == "Meeting Tomorrow"
        assert meeting_thread.participant_count >= 2  # john + jane at minimum

    def test_gmail_labels(self) -> None:
        """Email labels are parsed correctly."""
        entities = list(ingest_emails(FIXTURES_PATH))
        emails = [e for e in entities if isinstance(e, Email)]

        # Find starred email
        starred_email = next(
            (e for e in emails if e.is_starred),
            None,
        )
        assert starred_email is not None
        assert starred_email.subject == "Re: Meeting Tomorrow"

        # Find important email
        important_email = next(
            (e for e in emails if e.is_important),
            None,
        )
        assert important_email is not None
        assert important_email.subject == "Meeting Tomorrow"

    def test_spam_email(self) -> None:
        """Spam email has correct folder and flags."""
        entities = list(ingest_emails(FIXTURES_PATH))
        emails = [e for e in entities if isinstance(e, Email)]

        # Find spam email
        spam_email = next(
            (e for e in emails if e.subject == "You won a prize!"),
            None,
        )
        assert spam_email is not None
        assert spam_email.folder == EmailFolder.SPAM
        assert spam_email.is_spam is True
        assert spam_email.is_read is False  # has Unread label

    def test_sent_email(self) -> None:
        """Sent email has correct folder."""
        entities = list(ingest_emails(FIXTURES_PATH))
        emails = [e for e in entities if isinstance(e, Email)]

        # Find sent email
        sent_email = next(
            (e for e in emails if e.subject == "Weekly Report"),
            None,
        )
        assert sent_email is not None
        assert sent_email.folder == EmailFolder.SENT
        assert sent_email.is_sent is True

    def test_email_with_attachment(self) -> None:
        """Email with attachment has correct attachment count."""
        entities = list(ingest_emails(FIXTURES_PATH))
        emails = [e for e in entities if isinstance(e, Email)]

        # Find email with attachment
        report_email = next(
            (e for e in emails if e.subject == "Weekly Report"),
            None,
        )
        assert report_email is not None
        assert report_email.attachment_count == 1
        assert report_email.has_attachments is True

    def test_source_type(self) -> None:
        """All entities have correct source type."""
        entities = list(ingest_emails(FIXTURES_PATH))

        for entity in entities:
            assert entity.source_type == SourceType.GOOGLE_TAKEOUT

    def test_timestamp(self) -> None:
        """Email has correct timestamp."""
        entities = list(ingest_emails(FIXTURES_PATH))
        emails = [e for e in entities if isinstance(e, Email)]

        meeting_email = next(
            (e for e in emails if e.subject == "Meeting Tomorrow"),
            None,
        )
        assert meeting_email is not None
        assert meeting_email.occurred_at is not None
        assert meeting_email.occurred_at.year == 2024
        assert meeting_email.occurred_at.month == 1
        assert meeting_email.occurred_at.day == 16

    def test_date_filter_since(self) -> None:
        """Date filter 'since' excludes earlier emails."""
        filters = PipelineFilter(since=datetime(2024, 1, 17, tzinfo=UTC))
        entities = list(ingest_emails(FIXTURES_PATH, filters))
        emails = [e for e in entities if isinstance(e, Email)]

        # Should only include Jan 17+ emails
        for email in emails:
            assert email.occurred_at is not None
            assert email.occurred_at >= datetime(2024, 1, 17, tzinfo=UTC)

        # Jan 16 emails should be excluded
        jan_16_emails = [e for e in emails if e.subject and "Meeting" in e.subject]
        assert len(jan_16_emails) == 0

    def test_date_filter_until(self) -> None:
        """Date filter 'until' excludes later emails."""
        filters = PipelineFilter(until=datetime(2024, 1, 17, tzinfo=UTC))
        entities = list(ingest_emails(FIXTURES_PATH, filters))
        emails = [e for e in entities if isinstance(e, Email)]

        # Should exclude Jan 17+ emails
        for email in emails:
            assert email.occurred_at is not None
            assert email.occurred_at < datetime(2024, 1, 17, tzinfo=UTC)

        # Weekly Report (Jan 17) should be excluded
        report_emails = [e for e in emails if e.subject == "Weekly Report"]
        assert len(report_emails) == 0

    def test_empty_directory(self) -> None:
        """Empty directory yields no entities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entities = list(ingest_emails(Path(tmpdir)))
            assert entities == []

    def test_snippet_creation(self) -> None:
        """Email has snippet from body text."""
        entities = list(ingest_emails(FIXTURES_PATH))
        emails = [e for e in entities if isinstance(e, Email)]

        meeting_email = next(
            (e for e in emails if e.subject == "Meeting Tomorrow"),
            None,
        )
        assert meeting_email is not None
        assert meeting_email.snippet is not None
        assert "meet tomorrow" in meeting_email.snippet.lower()


class TestHelperFunctions:
    """Tests for mail parsing helper functions."""

    def test_parse_gmail_labels_simple(self) -> None:
        """Parse simple comma-separated labels."""
        labels = _parse_gmail_labels("Inbox,Important,Starred")
        assert labels == ["Inbox", "Important", "Starred"]

    def test_parse_gmail_labels_quoted(self) -> None:
        """Parse labels with quoted strings."""
        labels = _parse_gmail_labels('Inbox,"Custom Label",Sent')
        assert labels == ["Inbox", "Custom Label", "Sent"]

    def test_parse_gmail_labels_empty(self) -> None:
        """Empty string returns empty list."""
        labels = _parse_gmail_labels("")
        assert labels == []

    def test_parse_gmail_labels_single(self) -> None:
        """Single label without comma."""
        labels = _parse_gmail_labels("Inbox")
        assert labels == ["Inbox"]

    def test_labels_to_folder_trash(self) -> None:
        """Trash label has highest priority."""
        folder = _labels_to_folder(["Inbox", "Trash", "Important"])
        assert folder == EmailFolder.TRASH

    def test_labels_to_folder_spam(self) -> None:
        """Spam label takes precedence over inbox."""
        folder = _labels_to_folder(["Inbox", "Spam"])
        assert folder == EmailFolder.SPAM

    def test_labels_to_folder_sent(self) -> None:
        """Sent label maps to SENT folder."""
        folder = _labels_to_folder(["Sent"])
        assert folder == EmailFolder.SENT

    def test_labels_to_folder_inbox(self) -> None:
        """Inbox label maps to INBOX folder."""
        folder = _labels_to_folder(["Inbox", "Important"])
        assert folder == EmailFolder.INBOX

    def test_labels_to_folder_default(self) -> None:
        """Unknown labels default to INBOX."""
        folder = _labels_to_folder(["CustomLabel"])
        assert folder == EmailFolder.INBOX

    def test_labels_to_folder_all_mail(self) -> None:
        """All Mail label maps to ARCHIVE."""
        folder = _labels_to_folder(["All Mail"])
        assert folder == EmailFolder.ARCHIVE


class TestIntegrationWithStage:
    """Integration tests with GoogleTakeoutStage."""

    def test_stage_executes_email_ingestion(self) -> None:
        """Stage correctly routes to email ingestion."""
        from potluck.models.base import EntityType
        from potluck.pipeline.ingestion.google_takeout import GoogleTakeoutStage

        stage = GoogleTakeoutStage()

        # Execute for email only
        entities = list(
            stage.execute(
                FIXTURES_PATH,
                entity_types={EntityType.EMAIL},
            )
        )

        # Should get email entities (threads and emails)
        emails = [e for e in entities if isinstance(e, Email)]
        threads = [e for e in entities if isinstance(e, EmailThread)]

        assert len(emails) == 4
        assert len(threads) == 3
