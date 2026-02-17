"""WhatsApp message ingestion from decrypted msgstore.db."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from potluck.core.exceptions import IngestionError
from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.messages import ChatMessage, ChatThread, MessageType, ThreadType
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.utils.hashing import compute_content_hash

logger = get_logger(__name__)

# WhatsApp message type → Potluck MessageType mapping
WA_MESSAGE_TYPE_MAP: dict[int, MessageType] = {
    0: MessageType.TEXT,
    1: MessageType.IMAGE,
    2: MessageType.AUDIO,
    3: MessageType.VIDEO,
    7: MessageType.TEXT,  # Extended text (URL preview)
    9: MessageType.DOCUMENT,
    13: MessageType.IMAGE,  # GIF
    15: MessageType.STICKER,
    20: MessageType.STICKER,
}

# Message types to skip (system messages, ephemeral, etc.)
WA_SKIP_MESSAGE_TYPES = {90, 91, 92, 93, 94, 95}

# JID server types for chats we process
WA_CHAT_SERVERS = {"s.whatsapp.net", "g.us"}

# Batch size for streaming messages
MESSAGE_BATCH_SIZE = 5000


def ingest_messages(
    db_path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[ChatThread | ChatMessage]:
    """Ingest messages from WhatsApp msgstore.db.

    Memory-efficient streaming approach:
    1. Load all chats (small dataset).
    2. Yield ChatThread entities.
    3. Stream messages in batches, yielding ChatMessage entities.

    Args:
        db_path: Path to msgstore.db file.
        filters: Optional date range filters.

    Yields:
        ChatThread entities first, then ChatMessage entities.
    """
    conn = open_db(db_path)
    try:
        # Phase 1: Load chats and yield threads
        chat_threads, chat_row_to_thread = load_chats(conn)
        yield from chat_threads

        # Phase 2: Stream messages in batches
        valid_chat_ids = set(chat_row_to_thread.keys())
        yield from stream_messages(conn, valid_chat_ids, chat_row_to_thread, filters)

    finally:
        conn.close()


def count_messages(db_path: Path) -> tuple[int, int]:
    """Count messages and chats in a msgstore.db.

    Returns:
        Tuple of (message_count, chat_count).
    """
    conn = open_db(db_path)
    try:
        cursor = conn.cursor()

        # Count valid chats
        cursor.execute("""
            SELECT count(*)
            FROM chat c JOIN jid j ON c.jid_row_id = j._id
            WHERE j.server IN ('s.whatsapp.net', 'g.us')
        """)
        chat_count = cursor.fetchone()[0]

        # Count messages in valid chats
        cursor.execute("""
            SELECT count(*)
            FROM message m
            JOIN chat c ON m.chat_row_id = c._id
            JOIN jid j ON c.jid_row_id = j._id
            WHERE j.server IN ('s.whatsapp.net', 'g.us')
        """)
        msg_count = cursor.fetchone()[0]

        return msg_count, chat_count
    finally:
        conn.close()


def open_db(db_path: Path) -> sqlite3.Connection:
    """Open msgstore.db as read-only."""
    try:
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise IngestionError(f"Failed to open WhatsApp database {db_path}: {e}") from e


def load_chats(
    conn: sqlite3.Connection,
) -> tuple[list[ChatThread], dict[int, UUID]]:
    """Load chats from database and create ChatThread entities.

    Returns:
        Tuple of (thread_entities, chat_row_id_to_thread_uuid_map).
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c._id, c.subject, j.user, j.server, j.type
        FROM chat c
        JOIN jid j ON c.jid_row_id = j._id
        WHERE j.server IN ('s.whatsapp.net', 'g.us')
    """)

    threads: list[ChatThread] = []
    chat_row_to_thread: dict[int, UUID] = {}

    for row in cursor.fetchall():
        chat_id, subject, user, server, jid_type = row
        jid = f"{user}@{server}"

        if server == "g.us":
            thread_type = ThreadType.GROUP
            name = subject
        else:
            thread_type = ThreadType.DIRECT
            name = None

        thread = ChatThread(
            source_type=SourceType.WHATSAPP,
            source_id=jid,
            thread_type=thread_type,
            name=name,
        )

        threads.append(thread)
        chat_row_to_thread[chat_id] = thread.id

    # Update thread statistics with message counts
    _update_thread_stats(conn, threads, chat_row_to_thread)

    return threads, chat_row_to_thread


def _update_thread_stats(
    conn: sqlite3.Connection,
    threads: list[ChatThread],
    chat_row_to_thread: dict[int, UUID],
) -> None:
    """Update thread statistics (message count, timestamps) from message table."""
    thread_by_id = {t.id: t for t in threads}

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT m.chat_row_id, count(*), min(m.timestamp), max(m.timestamp)
        FROM message m
        WHERE m.chat_row_id IN ({})
        GROUP BY m.chat_row_id
    """.format(",".join("?" * len(chat_row_to_thread))),
        list(chat_row_to_thread.keys()),
    )

    for row in cursor.fetchall():
        chat_row_id, count, min_ts, max_ts = row
        thread_uuid = chat_row_to_thread.get(chat_row_id)
        if thread_uuid and thread_uuid in thread_by_id:
            thread = thread_by_id[thread_uuid]
            thread.message_count = count
            if min_ts:
                thread.first_message_at = _ms_to_datetime(min_ts)
            if max_ts:
                thread.last_message_at = _ms_to_datetime(max_ts)


def stream_messages(
    conn: sqlite3.Connection,
    valid_chat_ids: set[int],
    chat_row_to_thread: dict[int, UUID],
    filters: PipelineFilter | None,
) -> Iterator[ChatMessage]:
    """Stream messages in batches for memory efficiency."""
    placeholders = ",".join("?" * len(valid_chat_ids))
    query = f"""
        SELECT m._id, m.chat_row_id, m.from_me, m.text_data, m.timestamp,
               m.message_type, m.starred, j.user
        FROM message m
        LEFT JOIN jid j ON m.sender_jid_row_id = j._id
        WHERE m.chat_row_id IN ({placeholders})
        ORDER BY m.timestamp
    """

    cursor = conn.cursor()
    cursor.execute(query, list(valid_chat_ids))

    while True:
        batch = cursor.fetchmany(MESSAGE_BATCH_SIZE)
        if not batch:
            break

        for row in batch:
            msg_id, chat_row_id, from_me, text_data, timestamp, msg_type, starred, sender_user = row

            # Skip system messages
            if msg_type in WA_SKIP_MESSAGE_TYPES:
                continue

            occurred_at = _ms_to_datetime(timestamp) if timestamp else None

            # Apply date filters
            if filters and occurred_at:
                if filters.since and occurred_at < filters.since:
                    continue
                if filters.until and occurred_at >= filters.until:
                    continue

            thread_id = chat_row_to_thread.get(chat_row_id)
            if not thread_id:
                continue

            message_type = WA_MESSAGE_TYPE_MAP.get(msg_type, MessageType.OTHER)

            # Build content hash from chat row ID + timestamp + text
            thread_source = str(chat_row_id)
            content_hash = compute_content_hash(f"wa:{thread_source}:{timestamp}:{text_data or ''}")

            yield ChatMessage(
                source_type=SourceType.WHATSAPP,
                source_id=f"wa_msg_{msg_id}",
                content_hash=content_hash,
                occurred_at=occurred_at,
                thread_id=thread_id,
                message_type=message_type,
                content=text_data if text_data else None,
                is_from_me=bool(from_me),
                is_starred=bool(starred),
                sender_phone=sender_user,
                sender_name=sender_user,
            )


def _ms_to_datetime(timestamp_ms: int) -> datetime:
    """Convert millisecond epoch timestamp to UTC datetime."""
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
