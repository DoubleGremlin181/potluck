"""Google Chat/Hangouts message ingestion from Google Takeout.

Handles:
- Google Chat/Groups/*/messages.json: Chat messages
- Google Chat/Groups/*/group_info.json: Thread metadata
"""

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.messages import ChatMessage, ChatThread, ThreadType
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.utils.parsers import parse_datetime, parse_json

logger = get_logger(__name__)


def ingest_chat_messages(
    path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[ChatThread | ChatMessage]:
    """Ingest Google Chat messages from Google Takeout.

    Parses group_info.json and messages.json from Google Chat export.

    Args:
        path: Path to the extracted takeout directory.
        filters: Optional date range filters.

    Yields:
        ChatThread and ChatMessage entities.
    """
    chat_dir = _find_google_chat_dir(path)
    if not chat_dir:
        logger.debug("No Google Chat directory found in takeout")
        return

    groups_dir = chat_dir / "Groups"
    if not groups_dir.exists():
        logger.debug("No Groups directory found in Google Chat")
        return

    logger.info(f"Processing Google Chat from {chat_dir}")

    total_threads = 0
    total_messages = 0
    skipped_messages = 0

    # Process each conversation group
    for group_dir in groups_dir.iterdir():
        if not group_dir.is_dir():
            continue

        # Parse group info for thread metadata
        group_info_file = group_dir / "group_info.json"
        messages_file = group_dir / "messages.json"

        if not messages_file.exists():
            continue

        # Create thread from group info
        thread = _parse_thread(group_dir, group_info_file)
        if thread is None:
            continue

        yield thread
        total_threads += 1

        # Parse messages
        try:
            data = parse_json(messages_file)
        except Exception as e:
            logger.warning(f"Failed to parse {messages_file}: {e}")
            continue

        if not isinstance(data, dict):
            continue

        messages_list = data.get("messages", [])
        if not isinstance(messages_list, list):
            continue

        for msg_data in messages_list:
            if not isinstance(msg_data, dict):
                skipped_messages += 1
                continue

            try:
                message = _parse_message(msg_data, thread.id)
                if message is None:
                    skipped_messages += 1
                    continue

                # Apply date filters
                if filters:
                    if (
                        filters.since
                        and message.occurred_at
                        and message.occurred_at < filters.since
                    ):
                        skipped_messages += 1
                        continue
                    if (
                        filters.until
                        and message.occurred_at
                        and message.occurred_at >= filters.until
                    ):
                        skipped_messages += 1
                        continue

                yield message
                total_messages += 1
            except Exception as e:
                logger.warning(f"Failed to parse message: {e}")
                skipped_messages += 1

    logger.info(
        f"Processed {total_threads} threads, {total_messages} messages, skipped {skipped_messages}"
    )


def _parse_thread(group_dir: Path, group_info_file: Path) -> ChatThread | None:
    """Parse a chat thread from group directory and info file.

    Args:
        group_dir: Directory containing the conversation.
        group_info_file: Path to group_info.json file.

    Returns:
        ChatThread entity or None if parsing fails.
    """
    # Determine thread type from directory name
    dir_name = group_dir.name
    if dir_name.startswith("DM "):
        thread_type = ThreadType.DIRECT
        thread_name = None  # DMs don't have names
    elif dir_name.startswith("Space "):
        thread_type = ThreadType.GROUP
        thread_name = dir_name[6:]  # Remove "Space " prefix
    else:
        thread_type = ThreadType.GROUP
        thread_name = dir_name

    # Generate thread ID from directory name
    thread_id = uuid4()

    # Extract source_id from directory name (the unique ID part)
    source_id = dir_name

    # Parse group_info.json for additional metadata
    participant_count = 0
    if group_info_file.exists():
        try:
            group_data = parse_json(group_info_file)
            if isinstance(group_data, dict):
                members = group_data.get("members", [])
                if isinstance(members, list):
                    participant_count = len(members)

                # For named groups, use the group name
                if thread_type == ThreadType.GROUP:
                    thread_name = group_data.get("name") or thread_name
        except Exception as e:
            logger.debug(f"Could not parse group_info.json: {e}")

    return ChatThread(
        id=thread_id,
        source_type=SourceType.GOOGLE_TAKEOUT.value,
        source_id=source_id,
        thread_type=thread_type,
        name=thread_name,
        participant_count=participant_count if participant_count > 0 else None,
    )


def _parse_message(msg_data: dict[str, Any], thread_id: Any) -> ChatMessage | None:
    """Parse a single chat message.

    Args:
        msg_data: Message data dictionary from messages.json.
        thread_id: ID of the parent thread.

    Returns:
        ChatMessage entity or None if parsing fails.
    """
    # Get message text - skip empty messages
    text = msg_data.get("text", "")
    if not text:
        # Check for attached media or other content types
        attachments = msg_data.get("attached_files", [])
        if not attachments:
            return None
        # For attachment-only messages, create a placeholder description
        text = f"[{len(attachments)} attachment(s)]"

    # Get sender information
    creator = msg_data.get("creator", {})
    if not isinstance(creator, dict):
        creator = {}

    sender_name = creator.get("name")
    sender_email = creator.get("email")

    # Parse timestamp - format: "Thursday, March 14, 2024 at 10:30:15 AM UTC"
    created_date = msg_data.get("created_date")
    occurred_at = _parse_chat_timestamp(created_date)

    # Generate source_id from message data
    # Use combination of thread, timestamp, and sender for uniqueness
    source_id_parts = [str(thread_id), created_date or "", sender_email or sender_name or ""]
    source_id = hashlib.sha256("|".join(source_id_parts).encode()).hexdigest()[:32]

    # Compute content hash
    content_hash_parts = [text, created_date or "", sender_email or ""]
    content_hash = hashlib.sha256("|".join(content_hash_parts).encode()).hexdigest()

    return ChatMessage(
        source_type=SourceType.GOOGLE_TAKEOUT,
        source_id=source_id,
        content_hash=content_hash,
        thread_id=thread_id,
        sender_name=sender_name,
        # Store email in content_json for potential person linking
        content=text,
        content_json=f'{{"sender_email": "{sender_email}"}}' if sender_email else None,
        occurred_at=occurred_at,
    )


def _parse_chat_timestamp(date_str: str | None) -> datetime | None:
    """Parse Google Chat timestamp format.

    Format examples:
    - "Thursday, March 14, 2024 at 10:30:15 AM UTC"
    - "Wednesday, January 1, 2025 at 12:00:00 PM UTC"

    Args:
        date_str: Timestamp string from Google Chat.

    Returns:
        Parsed datetime or None if parsing fails.
    """
    if not date_str:
        return None

    # Try common date parser first
    result = parse_datetime(date_str)
    if result:
        return result

    # Try Google Chat specific format
    # "Thursday, March 14, 2024 at 10:30:15 AM UTC"
    try:
        # Remove day name and "at"
        date_str = date_str.replace(" at ", " ")
        # Parse the remaining format
        # Try multiple formats
        formats = [
            "%A, %B %d, %Y %I:%M:%S %p %Z",  # Thursday, March 14, 2024 10:30:15 AM UTC
            "%A, %B %d, %Y %I:%M:%S %p",  # Without timezone
            "%B %d, %Y %I:%M:%S %p %Z",  # Without day name
            "%B %d, %Y %I:%M:%S %p",  # Minimal
        ]
        for fmt in formats:
            try:
                # strptime doesn't handle timezone names well, strip UTC
                clean_str = date_str.replace(" UTC", "")
                dt = datetime.strptime(clean_str, fmt.replace(" %Z", ""))
                # Assume UTC if timezone was present in original
                if "UTC" in date_str:
                    dt = dt.replace(tzinfo=UTC)
                return dt
            except ValueError:
                continue
    except Exception:
        pass

    logger.debug(f"Could not parse chat timestamp: {date_str}")
    return None


def _find_google_chat_dir(path: Path) -> Path | None:
    """Find Google Chat directory in takeout."""
    candidates = [
        path / "Takeout" / "Google Chat",
        path / "Google Chat",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None
