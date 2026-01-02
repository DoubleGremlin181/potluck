"""Tests for Email, EmailThread, and EmailAttachment models."""

from uuid import UUID, uuid4

from potluck.models.base import SourceType
from potluck.models.email import Email, EmailAttachment, EmailFolder, EmailThread


class TestEmailModels:
    """Tests for Email, EmailThread, and EmailAttachment models."""

    def test_email_thread_creation(self) -> None:
        """EmailThread can be created."""
        thread = EmailThread(source_type="google_takeout")
        assert isinstance(thread.id, UUID)
        assert thread.email_count == 0
        assert thread.is_read is False

    def test_email_creation(self) -> None:
        """Email can be created with required fields."""
        email = Email(
            source_type=SourceType.GOOGLE_TAKEOUT,
            from_address="sender@example.com",
        )
        assert email.from_address == "sender@example.com"
        assert email.folder == EmailFolder.INBOX
        assert email.has_attachments is False

    def test_email_folder_enum(self) -> None:
        """EmailFolder enum has expected values."""
        expected = {
            "inbox",
            "sent",
            "drafts",
            "trash",
            "spam",
            "archive",
            "starred",
            "important",
            "custom",
        }
        actual = {f.value for f in EmailFolder}
        assert actual == expected

    def test_email_attachment_creation(self) -> None:
        """EmailAttachment can be created."""
        attachment = EmailAttachment(
            email_id=uuid4(),
            filename="document.pdf",
        )
        assert attachment.filename == "document.pdf"
        assert attachment.is_inline is False
