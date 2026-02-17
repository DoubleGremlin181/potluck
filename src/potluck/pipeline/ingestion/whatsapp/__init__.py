"""WhatsApp message ingestion stage.

Parses decrypted WhatsApp backup databases (msgstore.db) from
whatsapp-backup-downloader-decryptor.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar
from uuid import UUID

from potluck.core.logging import get_logger
from potluck.models.base import EntityType, IngestableEntity, SourceType
from potluck.pipeline.dtos import DetectionResult, PipelineFilter
from potluck.pipeline.ingestion.base import BaseIngestionStage
from potluck.pipeline.ingestion.registry import register
from potluck.pipeline.ingestion.whatsapp.media import count_media, ingest_media
from potluck.pipeline.ingestion.whatsapp.messages import (
    count_messages,
    load_chats,
    open_db,
    stream_messages,
)

logger = get_logger(__name__)


@register
class WhatsAppStage(BaseIngestionStage):
    """Ingestion stage for WhatsApp backup databases.

    Parses msgstore.db from whatsapp-backup-downloader-decryptor.
    Supports DM and group chats, message history, and media files.
    """

    SOURCE_TYPE: ClassVar[SourceType] = SourceType.WHATSAPP

    FILENAME_PATTERNS: ClassVar[list[str]] = [
        r"\d+_\d{8}-decrypted",  # "16506835325_20251222-decrypted" folder
        r"msgstore\.db",  # Direct database file
        r"WhatsApp Chat.*\.zip",  # Standard text export (future)
    ]

    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {
        EntityType.CHAT_MESSAGE,
        EntityType.MEDIA,
    }

    def detect(self, path: Path) -> DetectionResult:
        """Detect WhatsApp data and count entities."""
        db_path = _find_msgstore_db(path)
        if not db_path:
            return DetectionResult(entity_counts={}, metadata={})

        entity_counts: dict[EntityType, int] = {}
        metadata: dict[str, str] = {"source": "WhatsApp"}

        msg_count, chat_count = count_messages(db_path)
        if msg_count > 0:
            entity_counts[EntityType.CHAT_MESSAGE] = msg_count
            metadata["chats"] = str(chat_count)

        conn = open_db(db_path)
        try:
            media_count = count_media(conn)
            if media_count > 0:
                entity_counts[EntityType.MEDIA] = media_count
        finally:
            conn.close()

        return DetectionResult(entity_counts=entity_counts, metadata=metadata)

    def execute(
        self,
        path: Path,
        entity_types: set[EntityType] | None = None,
        filters: PipelineFilter | None = None,
    ) -> Iterator[IngestableEntity]:
        """Yield entities from WhatsApp backup.

        Ordering: ChatThread → Media → ChatMessage (FK dependencies).
        Media entities are yielded before their ChatMessages so that
        media_id foreign keys can be resolved during persistence.
        """
        db_path = _find_msgstore_db(path)
        if not db_path:
            logger.warning(f"No msgstore.db found at {path}")
            return

        types_to_process = (
            entity_types or self.SUPPORTED_ENTITY_TYPES
        ) & self.SUPPORTED_ENTITY_TYPES
        backup_root = _find_backup_root(db_path)

        conn = open_db(db_path)
        try:
            # Phase 1: Load chats and yield threads
            chat_threads, chat_row_to_thread = load_chats(conn)
            if EntityType.CHAT_MESSAGE in types_to_process:
                yield from chat_threads

            # Phase 2: Media files (yield before messages for FK ordering)
            msg_to_media: dict[int, UUID] = {}
            if EntityType.MEDIA in types_to_process:
                for media_entity, msg_row_id in ingest_media(conn, backup_root, chat_row_to_thread):
                    msg_to_media[msg_row_id] = media_entity.id
                    yield media_entity

            # Phase 3: Stream messages
            if EntityType.CHAT_MESSAGE in types_to_process:
                valid_chat_ids = set(chat_row_to_thread.keys())
                for message in stream_messages(conn, valid_chat_ids, chat_row_to_thread, filters):
                    # Link media to message if available
                    if message.source_id:
                        try:
                            msg_id = int(message.source_id.replace("wa_msg_", ""))
                            media_id = msg_to_media.get(msg_id)
                            if media_id:
                                message.media_id = media_id
                        except (ValueError, AttributeError) as e:
                            logger.debug(
                                f"Could not link media to message {message.source_id}: {e}"
                            )
                    yield message

        finally:
            conn.close()


def _find_msgstore_db(path: Path) -> Path | None:
    """Find msgstore.db in the given path.

    Checks common locations:
    - path itself (if it's the DB file)
    - path/Databases/msgstore.db (standard backup structure)
    - path/msgstore.db
    """
    if path.is_file() and path.name == "msgstore.db":
        return path

    candidates = [
        path / "Databases" / "msgstore.db",
        path / "msgstore.db",
    ]

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    return None


def _find_backup_root(db_path: Path) -> Path:
    """Determine the backup root directory from the database path.

    If DB is at backup_root/Databases/msgstore.db, root is backup_root.
    Otherwise, root is the DB's parent directory.
    """
    if db_path.parent.name == "Databases":
        return db_path.parent.parent
    return db_path.parent
