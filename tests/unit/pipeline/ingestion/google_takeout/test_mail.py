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
from potluck.pipeline.utils.parsers import parse_mbox


class TestEmailIngestion:
    """Tests for Gmail email ingestion."""

    def test_ingest_emails_from_fixtures(self, google_takeout_fixtures_path: Path) -> None:
        """Ingest emails from fixture files."""
        entities = list(ingest_emails(google_takeout_fixtures_path))

        # Separate entity types
        emails = [e for e in entities if isinstance(e, Email)]
        threads = [e for e in entities if isinstance(e, EmailThread)]

        # Should have 4 emails and 3 threads (2 emails share a thread)
        assert len(emails) == 4
        assert len(threads) == 3

    def test_email_basic_fields(self, google_takeout_fixtures_path: Path) -> None:
        """Email has correct basic fields."""
        entities = list(ingest_emails(google_takeout_fixtures_path))
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

    def test_email_threading(self, google_takeout_fixtures_path: Path) -> None:
        """Reply email has correct in_reply_to and references."""
        entities = list(ingest_emails(google_takeout_fixtures_path))
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

    def test_thread_creation(self, google_takeout_fixtures_path: Path) -> None:
        """Thread has correct metadata."""
        entities = list(ingest_emails(google_takeout_fixtures_path))
        threads = [e for e in entities if isinstance(e, EmailThread)]

        # Find thread by source_id
        meeting_thread = next(
            (t for t in threads if t.source_id and "1234567890123456789" in t.source_id),
            None,
        )
        assert meeting_thread is not None
        assert meeting_thread.subject == "Meeting Tomorrow"
        assert meeting_thread.participant_count >= 2  # john + jane at minimum

    def test_gmail_labels(self, google_takeout_fixtures_path: Path) -> None:
        """Email labels are parsed correctly."""
        entities = list(ingest_emails(google_takeout_fixtures_path))
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

    def test_spam_email(self, google_takeout_fixtures_path: Path) -> None:
        """Spam email has correct folder and flags."""
        entities = list(ingest_emails(google_takeout_fixtures_path))
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

    def test_sent_email(self, google_takeout_fixtures_path: Path) -> None:
        """Sent email has correct folder."""
        entities = list(ingest_emails(google_takeout_fixtures_path))
        emails = [e for e in entities if isinstance(e, Email)]

        # Find sent email
        sent_email = next(
            (e for e in emails if e.subject == "Weekly Report"),
            None,
        )
        assert sent_email is not None
        assert sent_email.folder == EmailFolder.SENT
        assert sent_email.is_sent is True

    def test_email_with_attachment(self, google_takeout_fixtures_path: Path) -> None:
        """Email with attachment has correct attachment count."""
        entities = list(ingest_emails(google_takeout_fixtures_path))
        emails = [e for e in entities if isinstance(e, Email)]

        # Find email with attachment
        report_email = next(
            (e for e in emails if e.subject == "Weekly Report"),
            None,
        )
        assert report_email is not None
        assert report_email.attachment_count == 1
        assert report_email.has_attachments is True

    def test_source_type(self, google_takeout_fixtures_path: Path) -> None:
        """All entities have correct source type."""
        entities = list(ingest_emails(google_takeout_fixtures_path))

        for entity in entities:
            assert entity.source_type == SourceType.GOOGLE_TAKEOUT

    def test_timestamp(self, google_takeout_fixtures_path: Path) -> None:
        """Email has correct timestamp."""
        entities = list(ingest_emails(google_takeout_fixtures_path))
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

    def test_date_filter_since(self, google_takeout_fixtures_path: Path) -> None:
        """Date filter 'since' excludes earlier emails."""
        filters = PipelineFilter(since=datetime(2024, 1, 17, tzinfo=UTC))
        entities = list(ingest_emails(google_takeout_fixtures_path, filters))
        emails = [e for e in entities if isinstance(e, Email)]

        # Should only include Jan 17+ emails
        for email in emails:
            assert email.occurred_at is not None
            assert email.occurred_at >= datetime(2024, 1, 17, tzinfo=UTC)

        # Jan 16 emails should be excluded
        jan_16_emails = [e for e in emails if e.subject and "Meeting" in e.subject]
        assert len(jan_16_emails) == 0

    def test_date_filter_until(self, google_takeout_fixtures_path: Path) -> None:
        """Date filter 'until' excludes later emails."""
        filters = PipelineFilter(until=datetime(2024, 1, 17, tzinfo=UTC))
        entities = list(ingest_emails(google_takeout_fixtures_path, filters))
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

    def test_snippet_creation(self, google_takeout_fixtures_path: Path) -> None:
        """Email has snippet from body text."""
        entities = list(ingest_emails(google_takeout_fixtures_path))
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


class TestEmailEdgeCases:
    """Tests for email MBOX edge cases."""

    def test_email_without_from_header(self) -> None:
        """Emails missing From header are skipped gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mail_dir = Path(tmpdir) / "Mail"
            mail_dir.mkdir(parents=True)

            # MBOX with email missing From header
            mbox_content = """From - Mon Jan 15 10:00:00 2024
Subject: No From Header
To: recipient@example.com
Date: Mon, 15 Jan 2024 10:00:00 +0000
X-GM-THRID: 123456789
X-Gmail-Labels: Inbox

This email has no From header.

From - Mon Jan 15 11:00:00 2024
From: valid@example.com
Subject: Valid Email
To: recipient@example.com
Date: Mon, 15 Jan 2024 11:00:00 +0000
X-GM-THRID: 987654321
X-Gmail-Labels: Inbox

This is a valid email.
"""
            (mail_dir / "Test.mbox").write_text(mbox_content)

            entities = list(ingest_emails(Path(tmpdir)))
            emails = [e for e in entities if isinstance(e, Email)]

            # Only valid email should be yielded
            assert len(emails) == 1
            assert emails[0].subject == "Valid Email"

    def test_email_with_malformed_date(self) -> None:
        """Emails with malformed dates have None occurred_at."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mail_dir = Path(tmpdir) / "Mail"
            mail_dir.mkdir(parents=True)

            mbox_content = """From - Mon Jan 15 10:00:00 2024
From: sender@example.com
Subject: Bad Date
To: recipient@example.com
Date: Not a valid date at all
X-GM-THRID: 123456789
X-Gmail-Labels: Inbox

Email with invalid date header.
"""
            (mail_dir / "Test.mbox").write_text(mbox_content)

            entities = list(ingest_emails(Path(tmpdir)))
            emails = [e for e in entities if isinstance(e, Email)]

            assert len(emails) == 1
            assert emails[0].occurred_at is None

    def test_email_with_encoded_headers(self) -> None:
        """Emails with MIME-encoded headers are decoded correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mail_dir = Path(tmpdir) / "Mail"
            mail_dir.mkdir(parents=True)

            # MIME encoded subject (UTF-8 base64)
            mbox_content = """From - Mon Jan 15 10:00:00 2024
From: sender@example.com
Subject: =?UTF-8?B?VGVzdCBTdWJqZWN0?=
To: recipient@example.com
Date: Mon, 15 Jan 2024 10:00:00 +0000
X-GM-THRID: 123456789
X-Gmail-Labels: Inbox

Email with encoded subject.
"""
            (mail_dir / "Test.mbox").write_text(mbox_content)

            entities = list(ingest_emails(Path(tmpdir)))
            emails = [e for e in entities if isinstance(e, Email)]

            assert len(emails) == 1
            assert emails[0].subject == "Test Subject"

    def test_empty_mbox_file(self) -> None:
        """Empty MBOX files are handled gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mail_dir = Path(tmpdir) / "Mail"
            mail_dir.mkdir(parents=True)
            (mail_dir / "Empty.mbox").write_text("")

            entities = list(ingest_emails(Path(tmpdir)))
            assert entities == []

    def test_mbox_with_only_whitespace(self) -> None:
        """MBOX files with only whitespace are handled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mail_dir = Path(tmpdir) / "Mail"
            mail_dir.mkdir(parents=True)
            (mail_dir / "Whitespace.mbox").write_text("   \n\n   \n")

            entities = list(ingest_emails(Path(tmpdir)))
            assert entities == []

    def test_email_with_nul_bytes_in_body(self) -> None:
        """NUL bytes in email body are stripped (PostgreSQL rejects \\x00 in text)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mail_dir = Path(tmpdir) / "Mail"
            mail_dir.mkdir(parents=True)

            # Create MBOX with NUL bytes embedded in the body
            mbox_content = (
                "From - Mon Jan 15 10:00:00 2024\n"
                "From: sender@example.com\n"
                "Subject: NUL Test\n"
                "To: recipient@example.com\n"
                "Date: Mon, 15 Jan 2024 10:00:00 +0000\n"
                "X-GM-THRID: 111222333\n"
                "X-Gmail-Labels: Inbox\n"
                "\n"
                "Hello\x00World\x00\x00End\n"
            )
            (mail_dir / "Test.mbox").write_bytes(mbox_content.encode("utf-8"))

            entities = list(ingest_emails(Path(tmpdir)))
            emails = [e for e in entities if isinstance(e, Email)]

            assert len(emails) == 1
            assert emails[0].body_text is not None
            assert "\x00" not in emails[0].body_text
            assert "HelloWorldEnd" in emails[0].body_text


class TestHeadersOnlyMode:
    """Tests for headers_only parsing mode used in the memory-efficient first pass."""

    def test_headers_only_skips_body(self, google_takeout_fixtures_path: Path) -> None:
        """headers_only=True skips body content."""
        mbox_file = google_takeout_fixtures_path / "Mail" / "test.mbox"
        messages = list(parse_mbox(mbox_file, headers_only=True))

        # Should still parse all messages
        assert len(messages) == 4

        # Bodies should be None
        for msg in messages:
            assert msg.body_plain is None
            assert msg.body_html is None

    def test_headers_only_preserves_headers(self, google_takeout_fixtures_path: Path) -> None:
        """headers_only=True still parses all header fields correctly."""
        mbox_file = google_takeout_fixtures_path / "Mail" / "test.mbox"
        messages = list(parse_mbox(mbox_file, headers_only=True))

        # First message should have all header fields
        msg = messages[0]
        assert msg.subject == "Meeting Tomorrow"
        assert msg.from_address == "john.doe@example.com"
        assert msg.from_name == "John Doe"
        assert msg.date is not None
        assert msg.message_id == "msg001@example.com"
        assert "jane.smith@example.com" in msg.to_addresses
        assert "bob@example.com" in msg.cc_addresses
        assert msg.headers.get("X-GM-THRID") == "1234567890123456789"
        assert msg.headers.get("X-Gmail-Labels") == "Inbox,Important"

    def test_headers_only_skips_attachments(self, google_takeout_fixtures_path: Path) -> None:
        """headers_only=True skips attachment parsing."""
        mbox_file = google_takeout_fixtures_path / "Mail" / "test.mbox"
        messages = list(parse_mbox(mbox_file, headers_only=True))

        # The multipart message (Weekly Report) should have no attachments
        report_msg = next(m for m in messages if m.subject == "Weekly Report")
        assert len(report_msg.attachments) == 0

    def test_full_parse_has_attachment_metadata(self, google_takeout_fixtures_path: Path) -> None:
        """Full parse stores attachment metadata without binary content."""
        mbox_file = google_takeout_fixtures_path / "Mail" / "test.mbox"
        messages = list(parse_mbox(mbox_file))

        report_msg = next(m for m in messages if m.subject == "Weekly Report")
        assert len(report_msg.attachments) == 1
        att = report_msg.attachments[0]
        assert att.filename == "report.pdf"
        assert att.content_type == "application/pdf"
        assert att.size > 0
        # Binary content should NOT be stored (memory optimization)
        assert att.content == b""


class TestTwoPassIngestion:
    """Tests for the memory-efficient two-pass ingestion approach."""

    def test_threads_yielded_before_emails(self, google_takeout_fixtures_path: Path) -> None:
        """Threads are yielded before any emails (required for FK integrity)."""
        entities = list(ingest_emails(google_takeout_fixtures_path))

        first_email_idx = next(i for i, e in enumerate(entities) if isinstance(e, Email))
        last_thread_idx = max(i for i, e in enumerate(entities) if isinstance(e, EmailThread))

        assert last_thread_idx < first_email_idx, "All threads must be yielded before any emails"

    def test_thread_stats_accurate(self, google_takeout_fixtures_path: Path) -> None:
        """Thread statistics are accurate from the first pass."""
        entities = list(ingest_emails(google_takeout_fixtures_path))
        threads = [e for e in entities if isinstance(e, EmailThread)]

        # Find the thread with 2 emails (Meeting Tomorrow thread)
        meeting_thread = next(
            t for t in threads if t.source_id and "1234567890123456789" in t.source_id
        )
        assert meeting_thread.email_count == 2
        assert meeting_thread.participant_count >= 2
        assert meeting_thread.first_email_at is not None
        assert meeting_thread.last_email_at is not None
        assert meeting_thread.first_email_at <= meeting_thread.last_email_at

    def test_email_thread_linkage(self, google_takeout_fixtures_path: Path) -> None:
        """Every email with a thread ID is linked to the correct thread."""
        entities = list(ingest_emails(google_takeout_fixtures_path))
        threads = {t.id: t for t in entities if isinstance(t, EmailThread)}
        emails = [e for e in entities if isinstance(e, Email)]

        for email in emails:
            if email.thread_id is not None:
                assert email.thread_id in threads, (
                    f"Email '{email.subject}' references non-existent thread"
                )


class TestIntegrationWithStage:
    """Integration tests with GoogleTakeoutStage."""

    def test_stage_executes_email_ingestion(self, google_takeout_fixtures_path: Path) -> None:
        """Stage correctly routes to email ingestion."""
        from potluck.models.base import EntityType
        from potluck.pipeline.ingestion.google_takeout import GoogleTakeoutStage

        stage = GoogleTakeoutStage()

        # Execute for email only
        entities = list(
            stage.execute(
                google_takeout_fixtures_path,
                entity_types={EntityType.EMAIL},
            )
        )

        # Should get email entities (threads and emails)
        emails = [e for e in entities if isinstance(e, Email)]
        threads = [e for e in entities if isinstance(e, EmailThread)]

        assert len(emails) == 4
        assert len(threads) == 3
