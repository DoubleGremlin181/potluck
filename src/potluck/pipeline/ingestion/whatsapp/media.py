"""WhatsApp media file ingestion from backup directory."""

import mimetypes
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.media import Media, MediaType
from potluck.pipeline.utils.hashing import compute_file_hash
from potluck.pipeline.utils.media import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)

logger = get_logger(__name__)

# Batch size for streaming media rows
MEDIA_BATCH_SIZE = 1000


def ingest_media(
    conn: sqlite3.Connection,
    backup_root: Path,
    chat_row_to_thread: dict[int, UUID],
) -> Iterator[tuple[Media, int]]:
    """Ingest media files linked to WhatsApp messages.

    Queries the message_media table to find files referenced by messages,
    resolves their paths relative to the backup root, and yields Media entities.

    Args:
        conn: SQLite connection to msgstore.db.
        backup_root: Root directory of the WhatsApp backup (parent of Databases/).
        chat_row_to_thread: Mapping from chat row ID to thread UUID.

    Yields:
        Tuples of (Media entity, message_row_id) for linking to ChatMessage.
    """
    valid_chat_ids = set(chat_row_to_thread.keys())
    if not valid_chat_ids:
        return

    placeholders = ",".join("?" * len(valid_chat_ids))
    query = f"""
        SELECT mm.message_row_id, mm.file_path, mm.file_size,
               m.chat_row_id, m.timestamp, m.message_type
        FROM message_media mm
        JOIN message m ON mm.message_row_id = m._id
        WHERE mm.file_path IS NOT NULL AND mm.file_path != ''
          AND m.chat_row_id IN ({placeholders})
    """

    cursor = conn.cursor()
    cursor.execute(query, list(valid_chat_ids))

    skipped = 0
    yielded = 0

    while True:
        batch = cursor.fetchmany(MEDIA_BATCH_SIZE)
        if not batch:
            break

        for row in batch:
            msg_row_id, file_path_str, db_file_size, chat_row_id, timestamp, msg_type = row

            # Resolve file path relative to backup root
            resolved_path = _resolve_media_path(backup_root, file_path_str)
            if resolved_path is None:
                skipped += 1
                continue

            # Determine media type from file extension
            suffix = resolved_path.suffix.lower()
            media_type = _get_media_type_for_wa(suffix, msg_type)

            # Get actual file size and hash
            try:
                file_size = resolved_path.stat().st_size
            except OSError as e:
                logger.debug(f"stat() failed for {resolved_path}, using db size: {e}")
                file_size = db_file_size

            if file_size == 0:
                continue

            try:
                file_hash = compute_file_hash(resolved_path)
            except (OSError, ValueError) as e:
                logger.warning(f"Failed to hash {resolved_path}: {e}")
                continue

            # Timestamp for occurred_at
            occurred_at = None
            if timestamp:
                occurred_at = datetime.fromtimestamp(timestamp / 1000, tz=UTC)

            mime_type, _ = mimetypes.guess_type(str(resolved_path))

            media = Media(
                source_type=SourceType.WHATSAPP,
                source_id=f"wa_media_{msg_row_id}",
                content_hash=file_hash,
                occurred_at=occurred_at,
                file_path=str(resolved_path.resolve()),
                original_filename=resolved_path.name,
                file_size=file_size,
                mime_type=mime_type,
                media_type=media_type,
                file_hash=file_hash,
            )

            yielded += 1
            yield media, msg_row_id

    if skipped > 0:
        logger.warning(f"WhatsApp media: {skipped} files could not be resolved, {yielded} yielded")


def count_media(conn: sqlite3.Connection) -> int:
    """Count media files referenced in the database."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT count(*)
        FROM message_media
        WHERE file_path IS NOT NULL AND file_path != ''
    """)
    result: int = cursor.fetchone()[0]
    return result


def _resolve_media_path(backup_root: Path, file_path_str: str) -> Path | None:
    """Resolve a media file path from the database to an actual file.

    WhatsApp stores relative paths like 'Media/WhatsApp Images/IMG-xxx.jpg'.
    We try resolving relative to the backup root.
    """
    # Try relative to backup root
    candidate = backup_root / file_path_str
    if candidate.is_file():
        return candidate

    # Try just the filename in common WhatsApp media subdirectories
    filename = Path(file_path_str).name
    for subdir in ["Media", "media", "."]:
        subdir_path = backup_root / subdir
        if not subdir_path.is_dir():
            continue
        matches = [p for p in subdir_path.rglob(filename) if p.is_file()]
        if matches:
            if len(matches) > 1:
                logger.debug(f"Multiple matches for {filename} in {subdir_path}, using first")
            return matches[0]

    logger.debug(f"Media file not found: {file_path_str}")
    return None


def _get_media_type_for_wa(suffix: str, wa_msg_type: int) -> MediaType:
    """Determine MediaType from file extension or WhatsApp message type."""
    if suffix in IMAGE_EXTENSIONS:
        return MediaType.IMAGE
    if suffix in VIDEO_EXTENSIONS:
        return MediaType.VIDEO
    if suffix in AUDIO_EXTENSIONS:
        return MediaType.AUDIO

    # Fall back to WhatsApp message type
    wa_type_map = {
        1: MediaType.IMAGE,
        2: MediaType.AUDIO,
        3: MediaType.VIDEO,
        9: MediaType.DOCUMENT,
        13: MediaType.IMAGE,  # GIF
        15: MediaType.IMAGE,  # Sticker
        20: MediaType.IMAGE,  # Sticker
    }
    return wa_type_map.get(wa_msg_type, MediaType.OTHER)
