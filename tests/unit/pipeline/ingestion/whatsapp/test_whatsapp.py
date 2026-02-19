"""Tests for WhatsApp message ingester."""

import sqlite3
from pathlib import Path

from potluck.models.base import EntityType, SourceType
from potluck.models.media import Media, MediaType
from potluck.models.messages import ChatMessage, ChatThread, MessageType, ThreadType
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.ingestion.whatsapp import WhatsAppStage
from potluck.pipeline.ingestion.whatsapp.messages import (
    WA_MESSAGE_TYPE_MAP,
    _ms_to_datetime,
    ingest_messages,
)


def _create_wa_db(db_path: Path) -> None:
    """Create a minimal WhatsApp msgstore.db with representative schema and data."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Create jid table (contact/group identifiers)
    cursor.execute("""
        CREATE TABLE jid (
            _id INTEGER PRIMARY KEY,
            user TEXT,
            server TEXT,
            type INTEGER
        )
    """)

    # Create chat table
    cursor.execute("""
        CREATE TABLE chat (
            _id INTEGER PRIMARY KEY,
            jid_row_id INTEGER,
            subject TEXT,
            FOREIGN KEY (jid_row_id) REFERENCES jid(_id)
        )
    """)

    # Create message table
    cursor.execute("""
        CREATE TABLE message (
            _id INTEGER PRIMARY KEY,
            chat_row_id INTEGER,
            from_me INTEGER,
            sender_jid_row_id INTEGER,
            text_data TEXT,
            timestamp INTEGER,
            message_type INTEGER,
            starred INTEGER DEFAULT 0,
            FOREIGN KEY (chat_row_id) REFERENCES chat(_id),
            FOREIGN KEY (sender_jid_row_id) REFERENCES jid(_id)
        )
    """)

    # Create message_media table
    cursor.execute("""
        CREATE TABLE message_media (
            message_row_id INTEGER,
            file_path TEXT,
            file_size INTEGER,
            FOREIGN KEY (message_row_id) REFERENCES message(_id)
        )
    """)

    # Insert JIDs: DM contact, group, newsletter (should be skipped)
    cursor.executemany(
        "INSERT INTO jid VALUES (?, ?, ?, ?)",
        [
            (1, "15551234567", "s.whatsapp.net", 0),  # DM contact
            (2, "120363123456", "g.us", 1),  # Group
            (3, "newsletter_123", "newsletter", 21),  # Newsletter (skip)
            (4, "15559876543", "s.whatsapp.net", 0),  # Another DM contact (sender)
            (5, "broadcast", "broadcast", 5),  # Broadcast (skip)
        ],
    )

    # Insert chats
    cursor.executemany(
        "INSERT INTO chat VALUES (?, ?, ?)",
        [
            (1, 1, None),  # DM with contact 1
            (2, 2, "Family Group"),  # Group chat
            (3, 3, "Newsletter Channel"),  # Newsletter (should be filtered)
            (4, 5, None),  # Broadcast (should be filtered)
        ],
    )

    # Insert messages (timestamps in milliseconds)
    # DM messages
    cursor.executemany(
        "INSERT INTO message VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 1, 1, 4, "Hello!", 1700000000000, 0, 0),  # Text from contact
            (2, 1, 0, None, "Hi there!", 1700000060000, 0, 0),  # Text from me
            (3, 1, 1, 4, None, 1700000120000, 1, 0),  # Image (no text)
            (4, 1, 0, None, "Check this out", 1700000180000, 7, 1),  # Extended text, starred
            # Group messages
            (5, 2, 1, 1, "Group hello", 1700001000000, 0, 0),  # From contact 1
            (6, 2, 1, 4, "Group reply", 1700001060000, 0, 0),  # From contact 4
            (7, 2, 0, None, "My group msg", 1700001120000, 0, 0),  # From me
            # System message (should be skipped)
            (8, 2, 0, None, None, 1700001180000, 90, 0),
            # Newsletter message (should be filtered by chat)
            (9, 3, 0, None, "Newsletter content", 1700002000000, 0, 0),
            # Messages with different media types
            (10, 1, 0, None, None, 1700002100000, 2, 0),  # Audio
            (11, 1, 0, None, None, 1700002200000, 3, 0),  # Video
            (12, 1, 0, None, None, 1700002300000, 9, 0),  # Document
            (13, 1, 0, None, None, 1700002400000, 15, 0),  # Sticker
        ],
    )

    # Insert message_media entries
    cursor.executemany(
        "INSERT INTO message_media VALUES (?, ?, ?)",
        [
            (3, "Media/WhatsApp Images/IMG-20231114-WA0001.jpg", 50000),
            (10, "Media/WhatsApp Audio/AUD-20231114-WA0001.opus", 12000),
            (11, "Media/WhatsApp Video/VID-20231114-WA0001.mp4", 500000),
        ],
    )

    conn.commit()
    conn.close()


def _create_wa_backup(tmp_path: Path) -> Path:
    """Create a minimal WhatsApp backup directory structure."""
    backup_dir = tmp_path / "16506835325_20251222-decrypted"
    db_dir = backup_dir / "Databases"
    db_dir.mkdir(parents=True)

    _create_wa_db(db_dir / "msgstore.db")

    # Create some media files
    media_dir = backup_dir / "Media" / "WhatsApp Images"
    media_dir.mkdir(parents=True)
    img = media_dir / "IMG-20231114-WA0001.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 10 + b"\xff\xd9")

    audio_dir = backup_dir / "Media" / "WhatsApp Audio"
    audio_dir.mkdir(parents=True)
    audio = audio_dir / "AUD-20231114-WA0001.opus"
    audio.write_bytes(b"\x00" * 50)

    video_dir = backup_dir / "Media" / "WhatsApp Video"
    video_dir.mkdir(parents=True)
    video = video_dir / "VID-20231114-WA0001.mp4"
    video.write_bytes(b"\x00" * 100)

    return backup_dir


class TestWhatsAppDetection:
    """Tests for WhatsAppStage.detect()."""

    def test_detect_messages(self, tmp_path: Path) -> None:
        """Detection counts messages and chats."""
        backup_dir = _create_wa_backup(tmp_path)

        stage = WhatsAppStage()
        result = stage.detect(backup_dir)

        assert EntityType.CHAT_MESSAGE in result.entity_counts
        # DM messages (1-4, 10-13 = 8) + group messages (5-8 = 4) = 12
        # count_messages() includes system msgs; newsletter/broadcast filtered by chat
        assert result.entity_counts[EntityType.CHAT_MESSAGE] == 12

    def test_detect_media(self, tmp_path: Path) -> None:
        """Detection counts media files."""
        backup_dir = _create_wa_backup(tmp_path)

        stage = WhatsAppStage()
        result = stage.detect(backup_dir)

        assert EntityType.MEDIA in result.entity_counts
        assert result.entity_counts[EntityType.MEDIA] == 3

    def test_detect_empty_directory(self, tmp_path: Path) -> None:
        """Empty directory returns no counts."""
        stage = WhatsAppStage()
        result = stage.detect(tmp_path)
        assert result.entity_counts == {}

    def test_detect_direct_db(self, tmp_path: Path) -> None:
        """Detection works when pointing directly to msgstore.db."""
        _create_wa_db(tmp_path / "msgstore.db")

        stage = WhatsAppStage()
        result = stage.detect(tmp_path)

        assert EntityType.CHAT_MESSAGE in result.entity_counts

    def test_detect_metadata(self, tmp_path: Path) -> None:
        """Detection metadata includes source and chat count."""
        backup_dir = _create_wa_backup(tmp_path)

        stage = WhatsAppStage()
        result = stage.detect(backup_dir)

        assert result.metadata["source"] == "WhatsApp"
        assert "chats" in result.metadata


class TestWhatsAppMessages:
    """Tests for WhatsApp message ingestion."""

    def test_ingest_yields_threads_then_messages(self, tmp_path: Path) -> None:
        """Ingestion yields ChatThread entities before ChatMessage entities."""
        _create_wa_db(tmp_path / "msgstore.db")

        entities = list(ingest_messages(tmp_path / "msgstore.db"))
        threads = [e for e in entities if isinstance(e, ChatThread)]

        # Should have 2 threads (DM + group; newsletter and broadcast filtered)
        assert len(threads) == 2

        # All threads should come before any messages
        thread_indices = [i for i, e in enumerate(entities) if isinstance(e, ChatThread)]
        msg_indices = [i for i, e in enumerate(entities) if isinstance(e, ChatMessage)]
        if thread_indices and msg_indices:
            assert max(thread_indices) < min(msg_indices)

    def test_dm_thread_type(self, tmp_path: Path) -> None:
        """DM chats are classified as DIRECT threads."""
        _create_wa_db(tmp_path / "msgstore.db")

        entities = list(ingest_messages(tmp_path / "msgstore.db"))
        threads = [e for e in entities if isinstance(e, ChatThread)]

        dm_threads = [t for t in threads if t.thread_type == ThreadType.DIRECT]
        assert len(dm_threads) == 1
        assert dm_threads[0].name is None
        assert dm_threads[0].source_type == SourceType.WHATSAPP

    def test_group_thread_type(self, tmp_path: Path) -> None:
        """Group chats are classified as GROUP threads with name."""
        _create_wa_db(tmp_path / "msgstore.db")

        entities = list(ingest_messages(tmp_path / "msgstore.db"))
        threads = [e for e in entities if isinstance(e, ChatThread)]

        group_threads = [t for t in threads if t.thread_type == ThreadType.GROUP]
        assert len(group_threads) == 1
        assert group_threads[0].name == "Family Group"

    def test_newsletter_and_broadcast_filtered(self, tmp_path: Path) -> None:
        """Newsletter and broadcast chats are not included."""
        _create_wa_db(tmp_path / "msgstore.db")

        entities = list(ingest_messages(tmp_path / "msgstore.db"))
        threads = [e for e in entities if isinstance(e, ChatThread)]

        # Only DM and group, not newsletter or broadcast
        assert len(threads) == 2
        source_ids = {t.source_id for t in threads if t.source_id}
        assert not any("newsletter" in sid for sid in source_ids)
        assert not any("broadcast" in sid for sid in source_ids)

    def test_message_fields(self, tmp_path: Path) -> None:
        """Message entity fields are populated correctly."""
        _create_wa_db(tmp_path / "msgstore.db")

        entities = list(ingest_messages(tmp_path / "msgstore.db"))
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        # Find the "Hello!" message
        hello_msg = next(m for m in messages if m.content == "Hello!")
        assert hello_msg.source_type == SourceType.WHATSAPP
        assert hello_msg.source_id == "wa_msg_1"
        assert hello_msg.message_type == MessageType.TEXT
        assert hello_msg.is_from_me is True  # from_me=1 in DB
        assert hello_msg.occurred_at is not None
        assert hello_msg.content_hash is not None
        assert hello_msg.sender_phone == "15559876543"

    def test_from_me_flag(self, tmp_path: Path) -> None:
        """Messages correctly identify from_me status."""
        _create_wa_db(tmp_path / "msgstore.db")

        entities = list(ingest_messages(tmp_path / "msgstore.db"))
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        # "Hi there!" is from_me=0 (contact's message)
        contact_msg = next(m for m in messages if m.content == "Hi there!")
        assert contact_msg.is_from_me is False

        # "Hello!" is from_me=1 (our message)
        our_msg = next(m for m in messages if m.content == "Hello!")
        assert our_msg.is_from_me is True

    def test_starred_flag(self, tmp_path: Path) -> None:
        """Starred messages are marked correctly."""
        _create_wa_db(tmp_path / "msgstore.db")

        entities = list(ingest_messages(tmp_path / "msgstore.db"))
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        starred = [m for m in messages if m.is_starred]
        assert len(starred) == 1
        assert starred[0].content == "Check this out"

    def test_message_type_mapping(self, tmp_path: Path) -> None:
        """WhatsApp message types map to correct MessageType."""
        _create_wa_db(tmp_path / "msgstore.db")

        entities = list(ingest_messages(tmp_path / "msgstore.db"))
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        # Find messages by source_id (maps to _id in DB)
        msg_by_id = {m.source_id: m for m in messages}

        assert msg_by_id["wa_msg_1"].message_type == MessageType.TEXT
        assert msg_by_id["wa_msg_3"].message_type == MessageType.IMAGE
        assert msg_by_id["wa_msg_4"].message_type == MessageType.TEXT  # Extended text
        assert msg_by_id["wa_msg_10"].message_type == MessageType.AUDIO
        assert msg_by_id["wa_msg_11"].message_type == MessageType.VIDEO
        assert msg_by_id["wa_msg_12"].message_type == MessageType.DOCUMENT
        assert msg_by_id["wa_msg_13"].message_type == MessageType.STICKER

    def test_system_messages_skipped(self, tmp_path: Path) -> None:
        """System messages (type 90+) are skipped."""
        _create_wa_db(tmp_path / "msgstore.db")

        entities = list(ingest_messages(tmp_path / "msgstore.db"))
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        # Message 8 is type 90 (system), should not appear
        source_ids = {m.source_id for m in messages}
        assert "wa_msg_8" not in source_ids

    def test_thread_message_counts(self, tmp_path: Path) -> None:
        """Thread statistics include message counts."""
        _create_wa_db(tmp_path / "msgstore.db")

        entities = list(ingest_messages(tmp_path / "msgstore.db"))
        threads = [e for e in entities if isinstance(e, ChatThread)]

        dm_thread = next(t for t in threads if t.thread_type == ThreadType.DIRECT)
        group_thread = next(t for t in threads if t.thread_type == ThreadType.GROUP)

        # DM has messages 1-4, 10-13 = 8 messages
        assert dm_thread.message_count == 8
        # Group has messages 5-8 = 4 messages (includes system msg in count)
        assert group_thread.message_count == 4

    def test_thread_timestamps(self, tmp_path: Path) -> None:
        """Thread first/last message timestamps are populated."""
        _create_wa_db(tmp_path / "msgstore.db")

        entities = list(ingest_messages(tmp_path / "msgstore.db"))
        threads = [e for e in entities if isinstance(e, ChatThread)]

        for thread in threads:
            assert thread.first_message_at is not None
            assert thread.last_message_at is not None
            assert thread.first_message_at <= thread.last_message_at

    def test_timestamp_conversion(self) -> None:
        """Millisecond epoch timestamps convert to UTC datetime."""
        dt = _ms_to_datetime(1700000000000)
        assert dt.year == 2023
        assert dt.month == 11
        assert dt.tzname() == "UTC"


class TestWhatsAppDateFilters:
    """Tests for date range filtering."""

    def test_since_filter(self, tmp_path: Path) -> None:
        """Messages before 'since' date are excluded."""
        _create_wa_db(tmp_path / "msgstore.db")

        # Filter to only messages after the group messages
        since = _ms_to_datetime(1700001000000)
        filters = PipelineFilter(since=since)
        entities = list(ingest_messages(tmp_path / "msgstore.db", filters))
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        # All DM messages (1-4) have earlier timestamps, should be excluded
        # Group messages 5-7 and DM messages 10-13 should remain
        source_ids = {m.source_id for m in messages}
        assert "wa_msg_1" not in source_ids
        assert "wa_msg_5" in source_ids

    def test_until_filter(self, tmp_path: Path) -> None:
        """Messages at or after 'until' date are excluded."""
        _create_wa_db(tmp_path / "msgstore.db")

        # Filter to only messages before the group messages
        until = _ms_to_datetime(1700001000000)
        filters = PipelineFilter(until=until)
        entities = list(ingest_messages(tmp_path / "msgstore.db", filters))
        messages = [e for e in entities if isinstance(e, ChatMessage)]

        source_ids = {m.source_id for m in messages}
        assert "wa_msg_1" in source_ids
        assert "wa_msg_5" not in source_ids


class TestWhatsAppStageExecution:
    """Tests for WhatsAppStage.execute() integration."""

    def test_full_ingestion(self, tmp_path: Path) -> None:
        """Full ingestion yields threads, media, and messages."""
        backup_dir = _create_wa_backup(tmp_path)

        stage = WhatsAppStage()
        entities = list(stage.execute(backup_dir))

        threads = [e for e in entities if isinstance(e, ChatThread)]
        messages = [e for e in entities if isinstance(e, ChatMessage)]
        media = [e for e in entities if isinstance(e, Media)]

        assert len(threads) == 2
        assert len(messages) > 0
        assert len(media) > 0

    def test_media_linked_to_messages(self, tmp_path: Path) -> None:
        """Media entities are linked to their ChatMessages via media_id."""
        backup_dir = _create_wa_backup(tmp_path)

        stage = WhatsAppStage()
        entities = list(stage.execute(backup_dir))

        messages = [e for e in entities if isinstance(e, ChatMessage)]
        media = [e for e in entities if isinstance(e, Media)]

        # Message 3 (image) should be linked to a media entity
        img_msg = next((m for m in messages if m.source_id == "wa_msg_3"), None)
        assert img_msg is not None
        assert img_msg.media_id is not None

        # The media_id should match one of our media entities
        media_ids = {m.id for m in media}
        assert img_msg.media_id in media_ids

    def test_media_before_messages(self, tmp_path: Path) -> None:
        """Media entities are yielded before their associated messages (FK ordering)."""
        backup_dir = _create_wa_backup(tmp_path)

        stage = WhatsAppStage()
        entities = list(stage.execute(backup_dir))

        media_indices = [i for i, e in enumerate(entities) if isinstance(e, Media)]
        msg_indices = [i for i, e in enumerate(entities) if isinstance(e, ChatMessage)]

        if media_indices and msg_indices:
            # All media should come before all messages
            assert max(media_indices) < min(msg_indices)

    def test_entity_type_filter_messages_only(self, tmp_path: Path) -> None:
        """Can filter to only ingest messages (no media)."""
        backup_dir = _create_wa_backup(tmp_path)

        stage = WhatsAppStage()
        entities = list(stage.execute(backup_dir, entity_types={EntityType.CHAT_MESSAGE}))

        assert any(isinstance(e, ChatThread) for e in entities)
        assert any(isinstance(e, ChatMessage) for e in entities)
        assert not any(isinstance(e, Media) for e in entities)

    def test_entity_type_filter_media_only(self, tmp_path: Path) -> None:
        """Can filter to only ingest media (no messages/threads)."""
        backup_dir = _create_wa_backup(tmp_path)

        stage = WhatsAppStage()
        entities = list(stage.execute(backup_dir, entity_types={EntityType.MEDIA}))

        assert not any(isinstance(e, ChatThread) for e in entities)
        assert not any(isinstance(e, ChatMessage) for e in entities)
        assert any(isinstance(e, Media) for e in entities)

    def test_media_entity_fields(self, tmp_path: Path) -> None:
        """Media entities have correct fields populated."""
        backup_dir = _create_wa_backup(tmp_path)

        stage = WhatsAppStage()
        entities = list(stage.execute(backup_dir))
        media = [e for e in entities if isinstance(e, Media)]

        # Find the image
        img_media = next((m for m in media if m.media_type == MediaType.IMAGE), None)
        assert img_media is not None
        assert img_media.source_type == SourceType.WHATSAPP
        assert img_media.file_hash is not None
        assert img_media.content_hash == img_media.file_hash
        assert img_media.file_size is not None
        assert img_media.file_size > 0
        assert img_media.occurred_at is not None
        assert img_media.original_filename is not None


class TestWhatsAppMessageTypeMap:
    """Tests for the WhatsApp message type mapping."""

    def test_text_types(self) -> None:
        assert WA_MESSAGE_TYPE_MAP[0] == MessageType.TEXT
        assert WA_MESSAGE_TYPE_MAP[7] == MessageType.TEXT

    def test_media_types(self) -> None:
        assert WA_MESSAGE_TYPE_MAP[1] == MessageType.IMAGE
        assert WA_MESSAGE_TYPE_MAP[2] == MessageType.AUDIO
        assert WA_MESSAGE_TYPE_MAP[3] == MessageType.VIDEO
        assert WA_MESSAGE_TYPE_MAP[9] == MessageType.DOCUMENT

    def test_sticker_types(self) -> None:
        assert WA_MESSAGE_TYPE_MAP[15] == MessageType.STICKER
        assert WA_MESSAGE_TYPE_MAP[20] == MessageType.STICKER

    def test_unknown_type_fallback(self) -> None:
        assert WA_MESSAGE_TYPE_MAP.get(999, MessageType.OTHER) == MessageType.OTHER
