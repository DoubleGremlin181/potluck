"""Parsing utilities for common file formats used in data ingestion."""

import email
import json
import mailbox
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from email.message import Message
from pathlib import Path
from typing import Any

import polars as pl

from potluck.core.exceptions import PipelineError
from potluck.core.logging import get_logger

logger = get_logger(__name__)


# Common date formats found in data exports
DATE_FORMATS = [
    # ISO 8601 variants
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    # US formats
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y",
    # European formats
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
    # RFC 2822 (email)
    "%a, %d %b %Y %H:%M:%S %z",
    "%d %b %Y %H:%M:%S %z",
]


def parse_datetime(value: str | int | float | None) -> datetime | None:
    """Parse a datetime from various formats.

    Handles ISO 8601, Unix timestamps, and common date formats.

    Args:
        value: String, Unix timestamp (int/float), or None.

    Returns:
        Parsed datetime or None if parsing fails.
    """
    if value is None:
        return None

    # Handle Unix timestamps (seconds or milliseconds)
    if isinstance(value, int | float):
        # Timestamps > 10 billion are likely milliseconds
        if value > 10_000_000_000:
            value = value / 1000
        try:
            return datetime.fromtimestamp(value)
        except (ValueError, OSError):
            return None

    if not isinstance(value, str):
        return None

    value = value.strip()
    if not value:
        return None

    # Try Unix timestamp as string
    try:
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000
        return datetime.fromtimestamp(ts)
    except ValueError:
        pass

    # Try common date formats
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    # Try ISO format with fromisoformat (handles more variants)
    try:
        # Handle Z suffix
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        pass

    logger.debug(f"Could not parse datetime: {value}")
    return None


def parse_json(
    path: Path,
    date_fields: list[str] | None = None,
) -> dict[str, Any] | list[Any]:
    """Parse a JSON file with optional date field conversion.

    Args:
        path: Path to the JSON file.
        date_fields: List of field names to parse as datetimes.

    Returns:
        Parsed JSON data (dict or list).

    Raises:
        PipelineError: If the file cannot be parsed.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data: dict[str, Any] | list[Any] = json.load(f)
    except json.JSONDecodeError as e:
        raise PipelineError(f"Invalid JSON in {path}: {e}") from e
    except OSError as e:
        raise PipelineError(f"Could not read {path}: {e}") from e

    if date_fields:
        _convert_date_fields(data, date_fields)

    return data


def _convert_date_fields(data: Any, date_fields: list[str]) -> None:
    """Recursively convert date fields in a data structure.

    Args:
        data: Data structure to modify in place.
        date_fields: Field names to convert to datetimes.
    """
    if isinstance(data, dict):
        for key, value in data.items():
            if key in date_fields and isinstance(value, str | int | float):
                data[key] = parse_datetime(value)
            elif isinstance(value, dict | list):
                _convert_date_fields(value, date_fields)
    elif isinstance(data, list):
        for item in data:
            _convert_date_fields(item, date_fields)


def parse_csv(
    path: Path,
    date_columns: list[str] | None = None,
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> Iterator[dict[str, Any]]:
    """Parse a CSV file and yield rows as dictionaries.

    Uses polars for efficient parsing with automatic type inference.
    Polars handles:
    - Automatic type detection (int, float, bool, string)
    - Null value detection (null, NA, N/A, etc.)
    - Memory-efficient streaming for large files

    Args:
        path: Path to the CSV file.
        date_columns: Column names to parse as datetimes.
        delimiter: CSV delimiter character.
        encoding: File encoding.

    Yields:
        Dict for each row with column names as keys.

    Raises:
        PipelineError: If the file cannot be parsed.
    """
    date_columns = date_columns or []

    try:
        # Use polars for efficient CSV parsing with type inference
        df = pl.read_csv(
            path,
            separator=delimiter,
            encoding=encoding if encoding != "utf-8" else "utf8",
            infer_schema_length=1000,  # Infer types from first 1000 rows
            null_values=["", "null", "NULL", "None", "none", "NA", "N/A", "n/a"],
            try_parse_dates=True,  # Auto-detect date columns
        )

        # Convert to Python dicts and yield rows
        for row in df.iter_rows(named=True):
            row_dict: dict[str, Any] = dict(row)

            # Parse specified date columns that polars didn't auto-detect
            for col in date_columns:
                if col in row_dict and row_dict[col] is not None:
                    value = row_dict[col]
                    # Only parse if it's still a string (not already a datetime)
                    if isinstance(value, str):
                        row_dict[col] = parse_datetime(value)

            yield row_dict

    except pl.exceptions.ComputeError as e:
        raise PipelineError(f"CSV parsing error in {path}: {e}") from e
    except OSError as e:
        raise PipelineError(f"Could not read {path}: {e}") from e


@dataclass
class MboxMessage:
    """Parsed email message from an MBOX file.

    This is a data transfer object, not a database model.
    The ingester is responsible for mapping this to the Email model.
    """

    message_id: str | None = None
    """Unique message ID from headers."""

    from_address: str | None = None
    """Sender email address."""

    from_name: str | None = None
    """Sender display name."""

    to_addresses: list[str] = field(default_factory=list)
    """Recipient email addresses."""

    cc_addresses: list[str] = field(default_factory=list)
    """CC recipient addresses."""

    bcc_addresses: list[str] = field(default_factory=list)
    """BCC recipient addresses."""

    subject: str | None = None
    """Email subject."""

    date: datetime | None = None
    """Email date."""

    body_plain: str | None = None
    """Plain text body."""

    body_html: str | None = None
    """HTML body."""

    headers: dict[str, str] = field(default_factory=dict)
    """All headers as key-value pairs."""

    attachments: list["MboxAttachment"] = field(default_factory=list)
    """List of attachments."""

    in_reply_to: str | None = None
    """Message ID this is a reply to."""

    references: list[str] = field(default_factory=list)
    """Referenced message IDs (thread chain)."""


@dataclass
class MboxAttachment:
    """Attachment from an email message."""

    filename: str | None = None
    """Original filename."""

    content_type: str | None = None
    """MIME content type."""

    content: bytes = field(default_factory=bytes, repr=False)
    """Attachment content."""

    size: int = 0
    """Size in bytes."""


def parse_mbox(path: Path) -> Iterator[MboxMessage]:
    """Parse an MBOX file and yield parsed messages.

    Messages that fail to parse are logged and skipped. To track the number
    of skipped messages, check the log output or count yielded vs total.

    Args:
        path: Path to the MBOX file.

    Yields:
        MboxMessage for each successfully parsed email in the file.

    Raises:
        PipelineError: If the file cannot be opened.
    """
    try:
        mbox = mailbox.mbox(str(path))
    except OSError as e:
        raise PipelineError(f"Could not open MBOX file {path}: {e}") from e

    skipped = 0
    total = 0
    try:
        for idx, msg in enumerate(mbox):
            total += 1
            try:
                yield _parse_email_message(msg)
            except (KeyError, ValueError, TypeError, AttributeError) as e:
                skipped += 1
                # Include message index and any available identifiers for debugging
                msg_id = msg.get("Message-ID", "<unknown>") if msg else "<unknown>"
                subject = msg.get("Subject", "<no subject>")[:50] if msg else "<unknown>"
                logger.warning(
                    f"Failed to parse email message {idx} "
                    f"(Message-ID={msg_id}, Subject={subject!r}): {e}",
                    exc_info=True,
                )
                continue
    finally:
        mbox.close()
        if skipped > 0:
            logger.info(
                f"Finished parsing {path.name}: {total - skipped}/{total} messages parsed, {skipped} skipped"
            )


def _parse_email_message(msg: Message) -> MboxMessage:
    """Parse a single email message.

    Args:
        msg: Email message object.

    Returns:
        Parsed MboxMessage.
    """
    result = MboxMessage()

    # Message ID
    result.message_id = msg.get("Message-ID", "").strip("<>")

    # From
    from_header = msg.get("From", "")
    result.from_address, result.from_name = _parse_email_address(from_header)

    # To, CC, BCC
    result.to_addresses = _parse_address_list(msg.get("To", ""))
    result.cc_addresses = _parse_address_list(msg.get("Cc", ""))
    result.bcc_addresses = _parse_address_list(msg.get("Bcc", ""))

    # Subject
    result.subject = _decode_header(msg.get("Subject", ""))

    # Date
    date_str = msg.get("Date", "")
    result.date = parse_datetime(date_str) if date_str else None

    # Threading
    result.in_reply_to = msg.get("In-Reply-To", "").strip("<>") or None
    references_header = msg.get("References", "")
    if references_header:
        result.references = [ref.strip("<>") for ref in references_header.split() if ref]

    # Headers
    for key in msg:
        result.headers[key] = msg.get(key, "")

    # Body and attachments
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            if "attachment" in content_disposition:
                _extract_attachment(part, result)
            elif content_type == "text/plain" and result.body_plain is None:
                result.body_plain = _get_text_content(part)
            elif content_type == "text/html" and result.body_html is None:
                result.body_html = _get_text_content(part)
    else:
        content_type = msg.get_content_type()
        if content_type == "text/plain":
            result.body_plain = _get_text_content(msg)
        elif content_type == "text/html":
            result.body_html = _get_text_content(msg)

    return result


def _parse_email_address(header: str) -> tuple[str | None, str | None]:
    """Parse an email address header into address and name.

    Args:
        header: Email header value (e.g., "John Doe <john@example.com>").

    Returns:
        Tuple of (email_address, display_name).
    """
    header = _decode_header(header)
    if not header:
        return None, None

    # Match "Name <email>" pattern
    match = re.match(r"^(.+?)\s*<(.+?)>$", header)
    if match:
        name = match.group(1).strip().strip("\"'")
        addr = match.group(2).strip()
        return addr, name if name else None

    # Just an email address
    if "@" in header:
        return header.strip(), None

    return None, None


def _parse_address_list(header: str) -> list[str]:
    """Parse a comma-separated list of email addresses.

    Args:
        header: Header value with multiple addresses.

    Returns:
        List of email addresses (without names).
    """
    header = _decode_header(header)
    if not header:
        return []

    addresses = []
    for part in header.split(","):
        addr, _ = _parse_email_address(part.strip())
        if addr:
            addresses.append(addr)

    return addresses


def _decode_header(header: str | None) -> str:
    """Decode an email header value.

    Handles RFC 2047 encoded-word syntax.

    Args:
        header: Header value to decode.

    Returns:
        Decoded string.
    """
    if not header:
        return ""

    try:
        decoded_parts = email.header.decode_header(header)
        parts = []
        for content, charset in decoded_parts:
            if isinstance(content, bytes):
                parts.append(content.decode(charset or "utf-8", errors="replace"))
            else:
                parts.append(content)
        return "".join(parts)
    except (ValueError, LookupError, UnicodeDecodeError) as e:
        logger.debug(f"Could not decode email header, using raw value: {e}")
        return str(header)


def _get_text_content(part: Message) -> str | None:
    """Extract text content from an email part.

    Args:
        part: Email message part.

    Returns:
        Decoded text content or None.
    """
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return None

        # get_payload with decode=True returns bytes
        if not isinstance(payload, bytes):
            return None

        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError, ValueError) as e:
        logger.debug(f"Could not extract text content from email part: {e}")
        return None


def _extract_attachment(part: Message, result: MboxMessage) -> None:
    """Extract an attachment from an email part.

    Args:
        part: Email message part.
        result: MboxMessage to add attachment to.
    """
    try:
        content = part.get_payload(decode=True)
        if content is None:
            return

        # get_payload with decode=True returns bytes
        if not isinstance(content, bytes):
            return

        filename = part.get_filename()
        if filename:
            filename = _decode_header(filename)

        attachment = MboxAttachment(
            filename=filename,
            content_type=part.get_content_type(),
            content=content,
            size=len(content),
        )
        result.attachments.append(attachment)
    except Exception as e:
        filename = part.get_filename() or "<unknown>"
        logger.warning(
            f"Failed to extract attachment '{filename}' from email: {e}. "
            "This attachment will not be available in the imported data."
        )
