"""MBOX email ingestion with RFC 2822 threading support.

Designed for Thunderbird, Apple Mail, and other standard MBOX files.
Uses In-Reply-To and References headers for threading (no Gmail-specific headers).
"""

import hashlib
import json
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.email import Email, EmailFolder, EmailThread
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.utils.email import compute_email_size, generate_snippet
from potluck.pipeline.utils.hashing import compute_content_hash
from potluck.pipeline.utils.parsers import MboxMessage, parse_mbox

logger = get_logger(__name__)

# Filename-to-folder mapping for Thunderbird and other clients
FOLDER_NAME_MAP: dict[str, EmailFolder] = {
    "inbox": EmailFolder.INBOX,
    "sent": EmailFolder.SENT,
    "sent messages": EmailFolder.SENT,
    "sent mail": EmailFolder.SENT,
    "drafts": EmailFolder.DRAFTS,
    "draft": EmailFolder.DRAFTS,
    "trash": EmailFolder.TRASH,
    "deleted messages": EmailFolder.TRASH,
    "deleted items": EmailFolder.TRASH,
    "spam": EmailFolder.SPAM,
    "junk": EmailFolder.SPAM,
    "junk e-mail": EmailFolder.SPAM,
    "archive": EmailFolder.ARCHIVE,
    "archives": EmailFolder.ARCHIVE,
    "starred": EmailFolder.STARRED,
    "flagged": EmailFolder.STARRED,
    "important": EmailFolder.IMPORTANT,
}

# Subject prefixes to strip when determining thread subject
_REPLY_PREFIX_RE = re.compile(r"^(Re|Fwd|Fw):\s*", re.IGNORECASE)

# Files to skip when scanning directories for MBOX files
_SKIP_EXTENSIONS = {".msf", ".dat", ".html", ".json", ".sqlite", ".db", ".sbd"}


def ingest_emails(
    mbox_paths: list[Path],
    folder_map: dict[Path, EmailFolder],
    filters: PipelineFilter | None = None,
) -> Iterator[Email | EmailThread]:
    """Ingest emails from MBOX files with RFC 2822 threading.

    Two-pass approach for memory efficiency:
    1. First pass (headers only): Build thread graph and collect statistics.
    2. Second pass (full parse): Yield threads first, then stream emails.

    Args:
        mbox_paths: List of MBOX file paths to process.
        folder_map: Mapping from MBOX file path to EmailFolder.
        filters: Optional date range filters.

    Yields:
        EmailThread entities first, then Email entities.
    """
    # --- First pass: header-only scan for thread statistics ---
    # Thread graph: message_id -> thread_root_id
    thread_roots: dict[str, str] = {}  # message_id -> root message_id
    thread_stats: dict[str, _ThreadStats] = {}  # root_id -> stats

    for mbox_path in mbox_paths:
        logger.debug(f"First pass (headers): {mbox_path}")

        for mbox_msg in parse_mbox(mbox_path, headers_only=True):
            if filters and not filters.passes(mbox_msg.date):
                continue

            msg_id = mbox_msg.message_id
            if not msg_id:
                continue

            # Find or create thread root using In-Reply-To and References
            root_id = _find_thread_root(msg_id, mbox_msg, thread_roots)

            if root_id not in thread_stats:
                # Strip reply prefixes for the thread subject
                subject = _strip_reply_prefix(mbox_msg.subject)
                thread_stats[root_id] = _ThreadStats(subject=subject)

            stats = thread_stats[root_id]
            stats.count += 1
            if mbox_msg.date:
                if stats.first_at is None or mbox_msg.date < stats.first_at:
                    stats.first_at = mbox_msg.date
                if stats.last_at is None or mbox_msg.date > stats.last_at:
                    stats.last_at = mbox_msg.date
            if mbox_msg.from_address:
                stats.participants.add(mbox_msg.from_address)
            stats.participants.update(mbox_msg.to_addresses)
            stats.participants.update(mbox_msg.cc_addresses)

    logger.info(f"First pass complete: {len(thread_stats)} threads discovered")

    # --- Yield thread entities ---
    threads: dict[str, EmailThread] = {}
    for root_id, stats in thread_stats.items():
        thread = EmailThread(
            id=uuid4(),
            source_type=SourceType.GENERIC,
            source_id=f"mbox_thread_{root_id}",
            content_hash=hashlib.sha256(root_id.encode()).hexdigest(),
            subject=stats.subject,
            participant_count=len(stats.participants),
            participant_emails=json.dumps(sorted(stats.participants))
            if stats.participants
            else None,
            email_count=stats.count,
            first_email_at=stats.first_at,
            last_email_at=stats.last_at,
        )
        threads[root_id] = thread
        yield thread

    # --- Second pass: full parse, yield emails ---
    for mbox_path in mbox_paths:
        folder = folder_map.get(mbox_path, EmailFolder.INBOX)
        logger.debug(f"Second pass (emails): {mbox_path}")

        for mbox_msg in parse_mbox(mbox_path):
            if filters and not filters.passes(mbox_msg.date):
                continue

            msg_id = mbox_msg.message_id
            if msg_id:
                root_id = thread_roots.get(msg_id, msg_id)
                parent_thread = threads.get(root_id)
            else:
                parent_thread = None

            email_entity = _create_email(mbox_msg, parent_thread, folder)
            if email_entity:
                yield email_entity


def find_mbox_files(path: Path) -> tuple[list[Path], dict[Path, EmailFolder]]:
    """Find MBOX files in a path, supporting both single files and directories.

    For directories, scans recursively for:
    - Files with .mbox extension
    - Files without extension whose first line starts with 'From ' (MBOX format)

    Args:
        path: File or directory to scan.

    Returns:
        Tuple of (mbox_file_paths, folder_map).
    """
    mbox_paths: list[Path] = []
    folder_map: dict[Path, EmailFolder] = {}

    if path.is_file():
        mbox_paths.append(path)
        folder_map[path] = _infer_folder(path)
        return mbox_paths, folder_map

    # Scan directory recursively
    for file_path in sorted(path.rglob("*")):
        if not file_path.is_file():
            continue

        # Skip known non-MBOX files
        if file_path.suffix.lower() in _SKIP_EXTENSIONS:
            continue

        if file_path.suffix.lower() == ".mbox":
            mbox_paths.append(file_path)
            folder_map[file_path] = _infer_folder(file_path)
        elif not file_path.suffix:
            # No extension — sniff content for MBOX format
            if _is_mbox_file(file_path):
                mbox_paths.append(file_path)
                folder_map[file_path] = _infer_folder(file_path)

    return mbox_paths, folder_map


def count_emails_in_mbox(path: Path) -> int:
    """Count 'From ' line separators to estimate email count."""
    count = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("From "):
                    count += 1
    except OSError as e:
        logger.warning(f"Failed to count emails in {path}: {e}")
    return count


def _is_mbox_file(path: Path) -> bool:
    """Check if a file looks like an MBOX file by reading its first line."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            first_line = f.readline()
            return first_line.startswith("From ")
    except OSError as e:
        logger.debug(f"Could not read {path} for MBOX detection: {e}")
        return False


def _infer_folder(path: Path) -> EmailFolder:
    """Infer EmailFolder from filename or path."""
    name = path.stem.lower()
    folder = FOLDER_NAME_MAP.get(name)
    if folder:
        return folder
    return EmailFolder.CUSTOM


def _strip_reply_prefix(subject: str | None) -> str | None:
    """Strip Re:/Fwd:/FW: prefixes from a subject line."""
    if not subject:
        return subject
    # Repeatedly strip prefixes
    result = subject
    while True:
        new_result = _REPLY_PREFIX_RE.sub("", result)
        if new_result == result:
            break
        result = new_result
    return result.strip() or subject


def _find_thread_root(
    msg_id: str,
    mbox_msg: MboxMessage,
    thread_roots: dict[str, str],
) -> str:
    """Find or establish the thread root for a message.

    Uses References (preferred) and In-Reply-To headers to build the thread graph.
    """
    # Check References header — first reference is typically the thread root
    if mbox_msg.references:
        root = mbox_msg.references[0]
        thread_roots[msg_id] = root
        return root

    # Fallback to In-Reply-To
    if mbox_msg.in_reply_to:
        # Check if the replied-to message already has a root
        root = thread_roots.get(mbox_msg.in_reply_to, mbox_msg.in_reply_to)
        thread_roots[msg_id] = root
        return root

    # No threading info — this message IS the root
    thread_roots[msg_id] = msg_id
    return msg_id


class _ThreadStats:
    """Lightweight statistics for a thread collected during first pass."""

    __slots__ = ("count", "first_at", "last_at", "participants", "subject")

    def __init__(self, subject: str | None = None) -> None:
        self.count: int = 0
        self.first_at: datetime | None = None
        self.last_at: datetime | None = None
        self.participants: set[str] = set()
        self.subject: str | None = subject


def _create_email(
    mbox_msg: MboxMessage,
    thread: EmailThread | None,
    folder: EmailFolder,
) -> Email | None:
    """Create an Email entity from a parsed MBOX message."""
    if not mbox_msg.from_address:
        logger.debug(f"Skipping email without from_address: message_id={mbox_msg.message_id}")
        return None

    body_text = mbox_msg.body_plain or ""
    snippet = generate_snippet(mbox_msg.body_plain)

    size_bytes = compute_email_size(mbox_msg.body_plain, mbox_msg.body_html)

    if mbox_msg.message_id:
        content_hash = compute_content_hash(f"mbox_email:{mbox_msg.message_id}")
    elif body_text:
        content_hash = compute_content_hash(body_text)
    else:
        content_hash = None

    return Email(
        id=uuid4(),
        source_type=SourceType.GENERIC,
        source_id=mbox_msg.message_id or f"mbox_{uuid4().hex[:12]}",
        content_hash=content_hash,
        occurred_at=mbox_msg.date,
        thread_id=thread.id if thread else None,
        message_id=mbox_msg.message_id,
        in_reply_to=mbox_msg.in_reply_to,
        references=json.dumps(mbox_msg.references) if mbox_msg.references else None,
        from_address=mbox_msg.from_address,
        from_name=mbox_msg.from_name,
        to_addresses=json.dumps(mbox_msg.to_addresses) if mbox_msg.to_addresses else None,
        cc_addresses=json.dumps(mbox_msg.cc_addresses) if mbox_msg.cc_addresses else None,
        bcc_addresses=json.dumps(mbox_msg.bcc_addresses) if mbox_msg.bcc_addresses else None,
        subject=mbox_msg.subject,
        body_text=mbox_msg.body_plain,
        body_html=mbox_msg.body_html,
        snippet=snippet,
        folder=folder,
        attachment_count=len(mbox_msg.attachments),
        has_attachments=len(mbox_msg.attachments) > 0,
        size_bytes=size_bytes,
    )
