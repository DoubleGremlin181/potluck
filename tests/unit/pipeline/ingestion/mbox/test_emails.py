"""Tests for MBOX email ingester."""

from datetime import UTC, datetime
from pathlib import Path

from potluck.models.base import EntityType, SourceType
from potluck.models.email import Email, EmailFolder, EmailThread
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.ingestion.mbox import MboxStage
from potluck.pipeline.ingestion.mbox.emails import (
    _infer_folder,
    _strip_reply_prefix,
    find_mbox_files,
)
from potluck.pipeline.utils.hashing import compute_content_hash

FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "mbox"
TEST_MBOX = FIXTURES_DIR / "test.mbox"


class TestMboxDetection:
    """Tests for MboxStage.detect()."""

    def test_detect_mbox_file(self) -> None:
        """Detection counts emails in a .mbox file."""
        stage = MboxStage()
        result = stage.detect(TEST_MBOX)

        assert EntityType.EMAIL in result.entity_counts
        assert result.entity_counts[EntityType.EMAIL] == 4

    def test_detect_directory(self) -> None:
        """Detection finds .mbox files in a directory."""
        stage = MboxStage()
        result = stage.detect(FIXTURES_DIR)

        assert EntityType.EMAIL in result.entity_counts
        assert result.entity_counts[EntityType.EMAIL] == 4

    def test_detect_empty_directory(self, tmp_path: Path) -> None:
        """Detection returns empty for directory without MBOX files."""
        stage = MboxStage()
        result = stage.detect(tmp_path)
        assert result.entity_counts == {}


class TestMboxThreading:
    """Tests for RFC 2822 email threading."""

    def test_threads_created(self) -> None:
        """Threads are created from In-Reply-To/References headers."""
        stage = MboxStage()
        entities = list(stage.execute(TEST_MBOX))

        threads = [e for e in entities if isinstance(e, EmailThread)]
        # Should have 2 threads: "Project Update" (3 messages) and "Meeting Tomorrow" (1 message)
        assert len(threads) == 2

    def test_thread_statistics(self) -> None:
        """Thread statistics are accurate from first pass."""
        stage = MboxStage()
        entities = list(stage.execute(TEST_MBOX))
        threads = [e for e in entities if isinstance(e, EmailThread)]

        # Find the "Project Update" thread (3 messages)
        project_thread = next(t for t in threads if t.subject == "Project Update")
        assert project_thread.email_count == 3
        assert project_thread.participant_count == 2  # alice and bob

        # Find the "Meeting Tomorrow" thread (1 message)
        meeting_thread = next(t for t in threads if t.subject == "Meeting Tomorrow")
        assert meeting_thread.email_count == 1

    def test_thread_subject_strips_prefix(self) -> None:
        """Thread subjects have Re:/Fwd: prefixes stripped."""
        stage = MboxStage()
        entities = list(stage.execute(TEST_MBOX))
        threads = [e for e in entities if isinstance(e, EmailThread)]

        # The thread should use "Project Update", not "Re: Project Update"
        subjects = {t.subject for t in threads}
        assert "Project Update" in subjects
        assert "Re: Project Update" not in subjects


class TestMboxEmailIngestion:
    """Tests for email entity creation."""

    def test_emails_created(self) -> None:
        """All emails are created as Email entities."""
        stage = MboxStage()
        entities = list(stage.execute(TEST_MBOX))

        emails = [e for e in entities if isinstance(e, Email)]
        assert len(emails) == 4

    def test_email_fields(self) -> None:
        """Email fields are mapped correctly."""
        stage = MboxStage()
        entities = list(stage.execute(TEST_MBOX))
        emails = [e for e in entities if isinstance(e, Email)]

        alice_email = next(e for e in emails if e.message_id == "msg001@example.com")
        assert alice_email.from_address == "alice@example.com"
        assert alice_email.from_name == "Alice Smith"
        assert alice_email.subject == "Project Update"
        assert alice_email.source_type == SourceType.GENERIC
        assert alice_email.body_text is not None
        assert "project update" in alice_email.body_text.lower()

    def test_emails_linked_to_threads(self) -> None:
        """Emails are linked to their parent threads."""
        stage = MboxStage()
        entities = list(stage.execute(TEST_MBOX))

        threads = {t.id: t for t in entities if isinstance(t, EmailThread)}
        emails = [e for e in entities if isinstance(e, Email)]

        # All emails should have a thread_id
        for email_entity in emails:
            assert email_entity.thread_id is not None
            assert email_entity.thread_id in threads

    def test_in_reply_to_preserved(self) -> None:
        """In-Reply-To header is preserved on email entities."""
        stage = MboxStage()
        entities = list(stage.execute(TEST_MBOX))
        emails = [e for e in entities if isinstance(e, Email)]

        reply = next(e for e in emails if e.message_id == "msg002@example.com")
        assert reply.in_reply_to == "msg001@example.com"


class TestFolderInference:
    """Tests for folder name inference."""

    def test_infer_inbox(self, tmp_path: Path) -> None:
        """'Inbox' file maps to INBOX folder."""
        p = tmp_path / "Inbox"
        p.touch()
        assert _infer_folder(p) == EmailFolder.INBOX

    def test_infer_sent(self, tmp_path: Path) -> None:
        """'Sent' file maps to SENT folder."""
        p = tmp_path / "Sent"
        p.touch()
        assert _infer_folder(p) == EmailFolder.SENT

    def test_infer_sent_messages(self, tmp_path: Path) -> None:
        """'Sent Messages' maps to SENT folder."""
        p = tmp_path / "Sent Messages"
        p.touch()
        assert _infer_folder(p) == EmailFolder.SENT

    def test_infer_trash(self, tmp_path: Path) -> None:
        """'Trash' maps to TRASH folder."""
        p = tmp_path / "Trash"
        p.touch()
        assert _infer_folder(p) == EmailFolder.TRASH

    def test_infer_custom(self, tmp_path: Path) -> None:
        """Unknown folder names map to CUSTOM."""
        p = tmp_path / "MyFolder.mbox"
        p.touch()
        assert _infer_folder(p) == EmailFolder.CUSTOM


class TestStripReplyPrefix:
    """Tests for subject prefix stripping."""

    def test_strip_re(self) -> None:
        assert _strip_reply_prefix("Re: Hello") == "Hello"

    def test_strip_fwd(self) -> None:
        assert _strip_reply_prefix("Fwd: Hello") == "Hello"

    def test_strip_multiple(self) -> None:
        assert _strip_reply_prefix("Re: Re: Fwd: Hello") == "Hello"

    def test_no_prefix(self) -> None:
        assert _strip_reply_prefix("Hello") == "Hello"

    def test_none(self) -> None:
        assert _strip_reply_prefix(None) is None


class TestFindMboxFiles:
    """Tests for MBOX file discovery."""

    def test_find_mbox_extension(self) -> None:
        """Files with .mbox extension are found."""
        paths, folder_map = find_mbox_files(FIXTURES_DIR)
        assert any(p.suffix == ".mbox" for p in paths)

    def test_find_extensionless_mbox(self, tmp_path: Path) -> None:
        """Extensionless files with 'From ' first line are detected as MBOX."""
        mbox_file = tmp_path / "Inbox"
        mbox_file.write_text("From sender@example.com Mon Jan 1 00:00:00 2024\nSubject: Test\n\n")

        paths, folder_map = find_mbox_files(tmp_path)
        assert mbox_file in paths
        assert folder_map[mbox_file] == EmailFolder.INBOX

    def test_skip_msf_files(self, tmp_path: Path) -> None:
        """Thunderbird .msf index files are skipped."""
        (tmp_path / "Inbox.msf").write_text("Thunderbird index")
        paths, _ = find_mbox_files(tmp_path)
        assert not any(p.suffix == ".msf" for p in paths)

    def test_single_file(self) -> None:
        """Single file path returns just that file."""
        paths, folder_map = find_mbox_files(TEST_MBOX)
        assert paths == [TEST_MBOX]


class TestMboxDateFiltering:
    """Tests for date range filtering."""

    def test_since_filter(self) -> None:
        """Emails before 'since' date are excluded."""
        stage = MboxStage()
        # Jan 16 00:00 — should exclude the two Jan 15 emails
        since = datetime(2024, 1, 16, tzinfo=UTC)
        entities = list(stage.execute(TEST_MBOX, filters=PipelineFilter(since=since)))

        emails = [e for e in entities if isinstance(e, Email)]
        assert all(e.occurred_at is not None and e.occurred_at >= since for e in emails)
        assert len(emails) == 2  # msg003 (Jan 16) and msg004 (Jan 17)

    def test_until_filter(self) -> None:
        """Emails after 'until' date are excluded."""
        stage = MboxStage()
        # Jan 16 00:00 — should exclude msg003 (Jan 16 09:00) and msg004 (Jan 17)
        until = datetime(2024, 1, 16, tzinfo=UTC)
        entities = list(stage.execute(TEST_MBOX, filters=PipelineFilter(until=until)))

        emails = [e for e in entities if isinstance(e, Email)]
        assert all(e.occurred_at is not None and e.occurred_at < until for e in emails)
        assert len(emails) == 2  # msg001 and msg002 (both Jan 15)


class TestMboxSkipNoFromAddress:
    """Test that emails without from_address are skipped."""

    def test_email_without_from_skipped(self, tmp_path: Path) -> None:
        """Emails missing the From header are skipped."""
        mbox_content = (
            "From - Thu Jan 15 10:00:00 2024\n"
            "Message-ID: <no-from@example.com>\n"
            "To: bob@example.com\n"
            "Subject: No From\n"
            "Date: Mon, 15 Jan 2024 10:00:00 +0000\n"
            'Content-Type: text/plain; charset="utf-8"\n'
            "\n"
            "This email has no From header.\n"
            "\n"
        )
        mbox_file = tmp_path / "test.mbox"
        mbox_file.write_text(mbox_content)

        stage = MboxStage()
        entities = list(stage.execute(mbox_file))

        emails = [e for e in entities if isinstance(e, Email)]
        assert len(emails) == 0


class TestMboxContentHashUsesComputeContentHash:
    """Tests that email content_hash uses compute_content_hash."""

    def test_email_content_hash_matches_compute_content_hash(self) -> None:
        """Email content_hash equals compute_content_hash('mbox_email:MESSAGE_ID')."""
        stage = MboxStage()
        entities = list(stage.execute(TEST_MBOX))
        emails = [e for e in entities if isinstance(e, Email)]

        for email_entity in emails:
            assert email_entity.message_id is not None
            expected = compute_content_hash(f"mbox_email:{email_entity.message_id}")
            assert email_entity.content_hash == expected

    def test_email_content_hash_deterministic(self) -> None:
        """Email content hashes are deterministic across runs."""
        stage = MboxStage()
        entities1 = list(stage.execute(TEST_MBOX))
        entities2 = list(stage.execute(TEST_MBOX))

        emails1 = sorted(
            [e for e in entities1 if isinstance(e, Email)],
            key=lambda e: e.message_id or "",
        )
        emails2 = sorted(
            [e for e in entities2 if isinstance(e, Email)],
            key=lambda e: e.message_id or "",
        )
        for e1, e2 in zip(emails1, emails2, strict=True):
            assert e1.content_hash == e2.content_hash

    def test_email_content_hash_is_not_raw_message_id(self) -> None:
        """Email content_hash is a SHA256 hex string, not the raw message_id."""
        stage = MboxStage()
        entities = list(stage.execute(TEST_MBOX))
        emails = [e for e in entities if isinstance(e, Email)]

        for email_entity in emails:
            # content_hash should be a 64-char hex SHA256, not the message_id itself
            assert email_entity.content_hash is not None
            assert len(email_entity.content_hash) == 64
            assert email_entity.content_hash != email_entity.message_id
