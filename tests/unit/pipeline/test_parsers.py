"""Tests for pipeline parsing utilities."""

from datetime import UTC
from pathlib import Path

from potluck.pipeline.utils.parsers import (
    MboxMessage,
    _extract_attachment,
    parse_datetime,
    parse_mbox,
)


class TestParseDatetimeUTCSuffix:
    """Tests for parse_datetime UTC suffix normalization."""

    def test_utc_suffix_normalized(self) -> None:
        """Datetime string ending with ' UTC' is parsed as UTC."""
        result = parse_datetime("2023-06-15 14:30:00 UTC")
        assert result is not None
        assert result.year == 2023
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 14
        assert result.minute == 30
        assert result.tzinfo is not None
        assert result.utcoffset() == UTC.utcoffset(None)

    def test_utc_suffix_different_date(self) -> None:
        """Another date with ' UTC' suffix is parsed correctly."""
        result = parse_datetime("2025-12-31 23:59:59 UTC")
        assert result is not None
        assert result.year == 2025
        assert result.month == 12
        assert result.day == 31
        assert result.hour == 23
        assert result.minute == 59
        assert result.second == 59

    def test_no_utc_suffix_still_works(self) -> None:
        """Dates without UTC suffix still parse normally."""
        result = parse_datetime("2024-06-15T12:30:00Z")
        assert result is not None
        assert result.year == 2024


class TestExtractAttachment:
    """Tests for _extract_attachment narrowed exception handling."""

    def test_valid_attachment_extracted(self) -> None:
        """A valid attachment is extracted with correct metadata."""
        import email.mime.base

        # Create a proper MIME attachment part
        part = email.mime.base.MIMEBase("application", "octet-stream")
        part.set_payload(b"Hello attachment content")
        part.add_header("Content-Disposition", "attachment", filename="test.txt")

        result = MboxMessage()
        _extract_attachment(part, result)

        assert len(result.attachments) == 1
        assert result.attachments[0].filename == "test.txt"
        assert result.attachments[0].content_type == "application/octet-stream"

    def test_attachment_extraction_handles_error_gracefully(self) -> None:
        """Attachment extraction that fails does not crash; attachment is skipped."""
        import email.message

        # Create a message part with broken encoding info to trigger an error
        part = email.message.Message()
        part["Content-Type"] = "application/octet-stream"
        part["Content-Disposition"] = "attachment; filename=broken.bin"
        part["Content-Transfer-Encoding"] = "quoted-printable"
        # Set a payload that is intentionally difficult to decode
        part.set_payload("=ZZ invalid QP", charset=None)

        result = MboxMessage()
        # This should not raise - the error is caught and the attachment is skipped
        _extract_attachment(part, result)
        # Either it succeeds and adds something, or it fails and logs a warning;
        # either way, no exception should propagate
        assert isinstance(result.attachments, list)


class TestParseMboxSkipBehavior:
    """Tests for parse_mbox narrowed exception handling (KeyError, ValueError)."""

    def test_valid_mbox_yields_messages(self, tmp_path: Path) -> None:
        """Valid MBOX file yields parsed messages."""
        mbox_content = (
            "From sender@example.com Mon Jan 15 10:00:00 2024\n"
            "From: sender@example.com\n"
            "To: receiver@example.com\n"
            "Subject: Test Subject\n"
            "Message-ID: <test001@example.com>\n"
            "Date: Mon, 15 Jan 2024 10:00:00 +0000\n"
            'Content-Type: text/plain; charset="utf-8"\n'
            "\n"
            "Test body.\n"
            "\n"
        )
        mbox_file = tmp_path / "test.mbox"
        mbox_file.write_text(mbox_content)

        messages = list(parse_mbox(mbox_file))
        assert len(messages) == 1
        assert messages[0].message_id == "test001@example.com"
        assert messages[0].subject == "Test Subject"

    def test_empty_mbox_yields_nothing(self, tmp_path: Path) -> None:
        """Empty MBOX file yields no messages."""
        mbox_file = tmp_path / "empty.mbox"
        mbox_file.write_text("")

        messages = list(parse_mbox(mbox_file))
        assert len(messages) == 0

    def test_headers_only_mode(self, tmp_path: Path) -> None:
        """headers_only=True skips body parsing."""
        mbox_content = (
            "From sender@example.com Mon Jan 15 10:00:00 2024\n"
            "From: sender@example.com\n"
            "Subject: Headers Only Test\n"
            "Message-ID: <headers01@example.com>\n"
            "Date: Mon, 15 Jan 2024 10:00:00 +0000\n"
            'Content-Type: text/plain; charset="utf-8"\n'
            "\n"
            "This body should not be parsed.\n"
            "\n"
        )
        mbox_file = tmp_path / "test.mbox"
        mbox_file.write_text(mbox_content)

        messages = list(parse_mbox(mbox_file, headers_only=True))
        assert len(messages) == 1
        assert messages[0].subject == "Headers Only Test"
        # In headers_only mode, body is not parsed
        assert messages[0].body_plain is None
