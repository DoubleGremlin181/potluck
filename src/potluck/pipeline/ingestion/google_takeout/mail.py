"""Gmail email ingestion from Google Takeout.

Handles:
- Mail/*.mbox: Email messages in MBOX format

Gmail MBOX files include Gmail-specific headers:
- X-GM-THRID: Gmail thread ID (decimal string)
- X-Gmail-Labels: Comma-separated labels (e.g., "Inbox,Important,Starred")
"""

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.email import Email, EmailFolder, EmailThread
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.utils.parsers import MboxMessage, parse_mbox

logger = get_logger(__name__)

# Constants
SNIPPET_MAX_LENGTH = 200  # Maximum length for email snippet preview

# Gmail label to folder mapping
LABEL_TO_FOLDER: dict[str, EmailFolder] = {
    "inbox": EmailFolder.INBOX,
    "sent": EmailFolder.SENT,
    "drafts": EmailFolder.DRAFTS,
    "draft": EmailFolder.DRAFTS,
    "trash": EmailFolder.TRASH,
    "spam": EmailFolder.SPAM,
    "archive": EmailFolder.ARCHIVE,
    "starred": EmailFolder.STARRED,
    "important": EmailFolder.IMPORTANT,
}


def ingest_emails(
    path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[Email | EmailThread]:
    """Ingest Gmail emails from Google Takeout.

    Scans for MBOX files and creates Email and EmailThread entities.
    Gmail-specific headers (X-GM-THRID, X-Gmail-Labels) are used for
    threading and labeling.

    Uses two-pass processing to ensure accurate thread statistics:
    1. First pass: Collect all emails and track stats per thread
    2. Second pass: Yield threads with correct stats, then yield emails

    Args:
        path: Path to the extracted takeout directory.
        filters: Optional date range filters.

    Yields:
        EmailThread and Email entities (threads first, then emails).
    """
    mail_dir = _find_mail_dir(path)
    if not mail_dir:
        logger.debug("No Mail directory found")
        return

    logger.info(f"Processing Gmail at {mail_dir}")

    # Track thread stats and data during first pass
    thread_stats: dict[str, _ThreadStats] = {}
    thread_data: dict[str, MboxMessage] = {}  # First message for each thread
    collected_emails: list[tuple[MboxMessage, str | None]] = []

    # First pass: Collect all emails and track thread statistics
    for mbox_file in sorted(mail_dir.glob("*.mbox")):
        logger.debug(f"Processing MBOX file: {mbox_file.name}")

        for mbox_msg in parse_mbox(mbox_file):
            # Get occurred_at from email date
            occurred_at = mbox_msg.date

            # Apply date filters
            if filters and occurred_at:
                if filters.since and occurred_at < filters.since:
                    continue
                if filters.until and occurred_at >= filters.until:
                    continue

            # Get Gmail thread ID
            thread_id = mbox_msg.headers.get("X-GM-THRID")

            # Track thread statistics
            if thread_id:
                if thread_id not in thread_stats:
                    thread_stats[thread_id] = _ThreadStats()
                    thread_data[thread_id] = mbox_msg

                stats = thread_stats[thread_id]
                stats.count += 1
                if occurred_at:
                    if stats.first_at is None or occurred_at < stats.first_at:
                        stats.first_at = occurred_at
                    if stats.last_at is None or occurred_at > stats.last_at:
                        stats.last_at = occurred_at

                # Track participants
                if mbox_msg.from_address:
                    stats.participants.add(mbox_msg.from_address)
                stats.participants.update(mbox_msg.to_addresses)
                stats.participants.update(mbox_msg.cc_addresses)

            collected_emails.append((mbox_msg, thread_id))

    # Second pass: Yield threads with accurate statistics
    threads: dict[str, EmailThread] = {}
    for thread_id, first_msg in thread_data.items():
        stats = thread_stats[thread_id]
        thread = _create_thread_with_stats(first_msg, thread_id, stats)
        threads[thread_id] = thread
        yield thread

    # Yield all emails
    for mbox_msg, thread_id in collected_emails:
        parent_thread = threads.get(thread_id) if thread_id else None
        email = _create_email(mbox_msg, parent_thread)
        if email:
            yield email


class _ThreadStats:
    """Statistics for a thread collected during first pass."""

    __slots__ = ("count", "first_at", "last_at", "participants")

    def __init__(self) -> None:
        from datetime import datetime

        self.count: int = 0
        self.first_at: datetime | None = None
        self.last_at: datetime | None = None
        self.participants: set[str] = set()


def _find_mail_dir(path: Path) -> Path | None:
    """Find Mail directory in takeout."""
    candidates = [
        path / "Takeout" / "Mail",
        path / "Mail",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _create_thread_with_stats(
    mbox_msg: MboxMessage,
    thread_id: str,
    stats: _ThreadStats,
) -> EmailThread:
    """Create an EmailThread entity with accurate statistics.

    Args:
        mbox_msg: First email message in the thread (for subject, labels).
        thread_id: Gmail thread ID.
        stats: Collected statistics for the thread.

    Returns:
        EmailThread entity with accurate counts and timestamps.
    """
    # Parse labels from first message
    labels = _parse_gmail_labels(mbox_msg.headers.get("X-Gmail-Labels", ""))

    return EmailThread(
        id=uuid4(),
        source_type=SourceType.GOOGLE_TAKEOUT,
        source_id=f"gmail_thread_{thread_id}",
        content_hash=thread_id,
        # Thread metadata
        subject=mbox_msg.subject,
        participant_count=len(stats.participants),
        participant_emails=json.dumps(sorted(stats.participants)) if stats.participants else None,
        # Thread statistics (accurate from first pass)
        email_count=stats.count,
        first_email_at=stats.first_at,
        last_email_at=stats.last_at,
        # Status
        is_read="unread" not in [lbl.lower() for lbl in labels],
        is_starred="starred" in [lbl.lower() for lbl in labels],
        is_important="important" in [lbl.lower() for lbl in labels],
        # Labels
        labels=json.dumps(labels) if labels else None,
    )


def _create_email(
    mbox_msg: MboxMessage,
    thread: EmailThread | None,
) -> Email | None:
    """Create an Email entity from an MBOX message.

    Args:
        mbox_msg: Parsed email message.
        thread: Parent thread if available.

    Returns:
        Email entity or None if from_address is missing.
    """
    # from_address is required
    if not mbox_msg.from_address:
        logger.debug(f"Skipping email without from_address: {mbox_msg.message_id}")
        return None

    # Parse labels and determine folder
    labels = _parse_gmail_labels(mbox_msg.headers.get("X-Gmail-Labels", ""))
    folder = _labels_to_folder(labels)

    # Check status flags from labels
    label_lower = [lbl.lower() for lbl in labels]
    is_starred = "starred" in label_lower
    is_important = "important" in label_lower
    is_read = "unread" not in label_lower
    is_draft = "draft" in label_lower or "drafts" in label_lower
    is_sent = "sent" in label_lower
    is_spam = "spam" in label_lower
    is_trash = "trash" in label_lower

    # Create snippet from body
    snippet = None
    body_text = mbox_msg.body_plain or ""
    if body_text:
        snippet = body_text[:SNIPPET_MAX_LENGTH].replace("\n", " ").strip()

    # Calculate size from body
    size_bytes = len(body_text.encode("utf-8")) if body_text else None
    if mbox_msg.body_html:
        size_bytes = (size_bytes or 0) + len(mbox_msg.body_html.encode("utf-8"))

    # Create content hash from message_id or body
    content_hash = mbox_msg.message_id
    if not content_hash and body_text:
        content_hash = hashlib.sha256(body_text.encode()).hexdigest()

    return Email(
        id=uuid4(),
        source_type=SourceType.GOOGLE_TAKEOUT,
        source_id=mbox_msg.message_id or f"gmail_{uuid4().hex[:12]}",
        content_hash=content_hash,
        occurred_at=mbox_msg.date,
        # Thread relationship
        thread_id=thread.id if thread else None,
        # Message identifiers
        message_id=mbox_msg.message_id,
        in_reply_to=mbox_msg.in_reply_to,
        references=json.dumps(mbox_msg.references) if mbox_msg.references else None,
        # Sender information
        from_address=mbox_msg.from_address,
        from_name=mbox_msg.from_name,
        # Recipients
        to_addresses=json.dumps(mbox_msg.to_addresses) if mbox_msg.to_addresses else None,
        cc_addresses=json.dumps(mbox_msg.cc_addresses) if mbox_msg.cc_addresses else None,
        bcc_addresses=json.dumps(mbox_msg.bcc_addresses) if mbox_msg.bcc_addresses else None,
        # Content
        subject=mbox_msg.subject,
        body_text=mbox_msg.body_plain,
        body_html=mbox_msg.body_html,
        snippet=snippet,
        # Email metadata
        folder=folder,
        labels=json.dumps(labels) if labels else None,
        # Status flags
        is_read=is_read,
        is_starred=is_starred,
        is_important=is_important,
        is_draft=is_draft,
        is_sent=is_sent,
        is_spam=is_spam,
        is_trash=is_trash,
        # Attachments
        attachment_count=len(mbox_msg.attachments),
        has_attachments=len(mbox_msg.attachments) > 0,
        # Size
        size_bytes=size_bytes,
    )


def _parse_gmail_labels(labels_header: str) -> list[str]:
    """Parse X-Gmail-Labels header.

    Gmail labels are comma-separated but can contain quoted strings
    for labels with special characters.

    Args:
        labels_header: X-Gmail-Labels header value.

    Returns:
        List of label strings.
    """
    if not labels_header:
        return []

    labels: list[str] = []
    current = ""
    in_quotes = False

    for char in labels_header:
        if char == '"':
            in_quotes = not in_quotes
        elif char == "," and not in_quotes:
            label = current.strip().strip('"')
            if label:
                labels.append(label)
            current = ""
        else:
            current += char

    # Add last label
    label = current.strip().strip('"')
    if label:
        labels.append(label)

    return labels


def _labels_to_folder(labels: list[str]) -> EmailFolder:
    """Determine primary folder from Gmail labels.

    Priority: Trash > Spam > Drafts > Sent > Inbox > Archive

    Args:
        labels: List of Gmail labels.

    Returns:
        Primary EmailFolder.
    """
    label_lower = {lbl.lower() for lbl in labels}

    # Priority order for folder determination
    if "trash" in label_lower:
        return EmailFolder.TRASH
    if "spam" in label_lower:
        return EmailFolder.SPAM
    if "draft" in label_lower or "drafts" in label_lower:
        return EmailFolder.DRAFTS
    if "sent" in label_lower:
        return EmailFolder.SENT
    if "inbox" in label_lower:
        return EmailFolder.INBOX
    if "archive" in label_lower or "all mail" in label_lower:
        return EmailFolder.ARCHIVE

    # Check for known folder labels
    for label in labels:
        folder = LABEL_TO_FOLDER.get(label.lower())
        if folder:
            return folder

    # Default to INBOX
    return EmailFolder.INBOX
