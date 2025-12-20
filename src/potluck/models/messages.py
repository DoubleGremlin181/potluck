"""Chat message models for messaging data from various platforms."""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from potluck.models.base import TimestampedEntity, _utc_now

if TYPE_CHECKING:
    pass


class MessageType(str, Enum):
    """Type of chat message."""

    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    STICKER = "sticker"
    LOCATION = "location"
    CONTACT = "contact"
    POLL = "poll"
    SYSTEM = "system"  # Join/leave notifications, etc.
    DELETED = "deleted"
    OTHER = "other"


class ThreadType(str, Enum):
    """Type of chat thread."""

    DIRECT = "direct"  # One-on-one conversation
    GROUP = "group"  # Group chat
    CHANNEL = "channel"  # Broadcast channel
    COMMUNITY = "community"  # Community/forum


class ChatThread(SQLModel, table=True):
    """Conversation thread container for messaging platforms.

    Works across WhatsApp, Telegram, SMS, and other messaging services.
    """

    __tablename__ = "chat_threads"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the thread",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the thread was created in the database",
    )
    updated_at: datetime = Field(
        default_factory=_utc_now,
        sa_column_kwargs={"onupdate": _utc_now},
        description="When the thread was last updated",
    )
    source_type: str = Field(
        description="Source platform (e.g., whatsapp, telegram)",
    )
    source_id: str | None = Field(
        default=None,
        index=True,
        description="Original thread ID from the source",
    )

    # Thread metadata
    thread_type: ThreadType = Field(
        default=ThreadType.DIRECT,
        description="Type of thread (direct, group, channel)",
    )
    name: str | None = Field(
        default=None,
        description="Thread name (for groups/channels)",
    )
    description: str | None = Field(
        default=None,
        description="Thread description",
    )

    # Participant information
    participant_count: int | None = Field(
        default=None,
        description="Number of participants in the thread",
    )
    participant_ids: str | None = Field(
        default=None,
        description="JSON-encoded list of participant Person IDs",
    )

    # Thread timestamps
    first_message_at: datetime | None = Field(
        default=None,
        description="Timestamp of the first message",
    )
    last_message_at: datetime | None = Field(
        default=None,
        index=True,
        description="Timestamp of the last message",
    )
    message_count: int = Field(
        default=0,
        description="Total number of messages in the thread",
    )

    # Archive/mute status
    is_archived: bool = Field(
        default=False,
        description="Whether the thread is archived",
    )
    is_muted: bool = Field(
        default=False,
        description="Whether notifications are muted",
    )

    # Relationships
    messages: list["ChatMessage"] = Relationship(back_populates="thread")


class ChatMessage(TimestampedEntity, table=True):
    """Individual chat message with sender, content, and metadata.

    Stores raw text content for full-text searchability.
    """

    __tablename__ = "chat_messages"

    # Thread relationship
    thread_id: UUID = Field(
        foreign_key="chat_threads.id",
        index=True,
        description="The thread this message belongs to",
    )

    # Sender information
    sender_id: UUID | None = Field(
        default=None,
        foreign_key="people.id",
        index=True,
        description="Person who sent the message",
    )
    sender_name: str | None = Field(
        default=None,
        description="Sender name at time of message (for history)",
    )
    sender_phone: str | None = Field(
        default=None,
        description="Sender phone number (for WhatsApp/SMS)",
    )

    # Message content
    message_type: MessageType = Field(
        default=MessageType.TEXT,
        description="Type of message content",
    )
    content: str | None = Field(
        default=None,
        description="Text content of the message (stored for FTS)",
    )
    content_json: str | None = Field(
        default=None,
        description="JSON-encoded structured content (polls, contacts, etc.)",
    )

    # Media attachment
    media_id: UUID | None = Field(
        default=None,
        foreign_key="media.id",
        description="Attached media file if any",
    )
    media_caption: str | None = Field(
        default=None,
        description="Caption for attached media",
    )

    # Reply information
    reply_to_id: UUID | None = Field(
        default=None,
        foreign_key="chat_messages.id",
        description="Message this is replying to",
    )
    forwarded_from: str | None = Field(
        default=None,
        description="Original sender if forwarded",
    )

    # Message metadata
    is_from_me: bool = Field(
        default=False,
        description="Whether this message was sent by the data owner",
    )
    is_read: bool = Field(
        default=False,
        description="Whether the message has been read",
    )
    is_starred: bool = Field(
        default=False,
        description="Whether the message is starred/favorited",
    )
    is_deleted: bool = Field(
        default=False,
        description="Whether the message was deleted",
    )

    # Reactions
    reactions: str | None = Field(
        default=None,
        description="JSON-encoded reactions {emoji: [person_ids]}",
    )

    # Relationships
    thread: ChatThread = Relationship(back_populates="messages")


class ChatThreadParticipant(SQLModel, table=True):
    """Participant in a chat thread.

    Links Person records to ChatThread with role information.
    """

    __tablename__ = "chat_thread_participants"

    thread_id: UUID = Field(
        foreign_key="chat_threads.id",
        primary_key=True,
        description="The chat thread",
    )
    person_id: UUID = Field(
        foreign_key="people.id",
        primary_key=True,
        description="The participant",
    )
    role: str | None = Field(
        default=None,
        description="Role in the thread (admin, member, etc.)",
    )
    nickname: str | None = Field(
        default=None,
        description="Nickname in this thread",
    )
    joined_at: datetime | None = Field(
        default=None,
        description="When they joined the thread",
    )
    left_at: datetime | None = Field(
        default=None,
        description="When they left the thread (if applicable)",
    )
    is_active: bool = Field(
        default=True,
        description="Whether they are currently active in the thread",
    )
