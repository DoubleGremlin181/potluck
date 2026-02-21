"""Email models for email data management."""

from datetime import datetime
from enum import Enum
from typing import ClassVar
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import Field, Relationship

from potluck.core.constants import MULTIMODAL_EMBEDDING_DIM, TEXT_EMBEDDING_DIM
from potluck.models.base import BaseEntity, SimpleEntity, TimestampedEntity, enum_field


class EmailFolder(str, Enum):
    """Standard email folder types.

    When folder information is unknown, default to INBOX.
    """

    INBOX = "inbox"
    SENT = "sent"
    DRAFTS = "drafts"
    TRASH = "trash"
    SPAM = "spam"
    ARCHIVE = "archive"
    STARRED = "starred"
    IMPORTANT = "important"
    CUSTOM = "custom"


class EmailThread(BaseEntity, table=True):
    """Email conversation thread container.

    Groups related emails by conversation ID or subject threading.
    """

    __tablename__ = "email_threads"

    # Thread metadata
    subject: str | None = Field(
        default=None,
        description="Subject line of the thread",
    )
    participant_count: int = Field(
        default=0,
        description="Number of unique participants",
    )
    participant_emails: str | None = Field(
        default=None,
        description="JSON-encoded list of participant email addresses",
    )

    # Thread statistics
    email_count: int = Field(
        default=0,
        description="Number of emails in the thread",
    )
    first_email_at: datetime | None = Field(
        default=None,
        description="Timestamp of the first email",
    )
    last_email_at: datetime | None = Field(
        default=None,
        index=True,
        description="Timestamp of the last email",
    )

    # Status
    is_read: bool = Field(
        default=False,
        description="Whether all emails in thread are read",
    )
    is_starred: bool = Field(
        default=False,
        description="Whether the thread is starred",
    )
    is_important: bool = Field(
        default=False,
        description="Whether the thread is marked important",
    )

    # Labels
    labels: str | None = Field(
        default=None,
        description="JSON-encoded list of labels/folders",
    )

    # Relationships
    emails: list["Email"] = Relationship(back_populates="thread")


class Email(TimestampedEntity, table=True):
    """Individual email with sender, recipients, subject, and body.

    Stores raw text content for full-text searchability.
    """

    __tablename__ = "emails"

    # Search configuration - subject is priority, body_text auto-discovered
    __searchable__: ClassVar[bool] = True
    __search_exclude_fields__: ClassVar[set[str]] = set()
    __search_priority_fields__: ClassVar[set[str]] = {"subject"}
    __search_date_fields__: ClassVar[set[str]] = {"occurred_at"}

    def to_text_repr(self) -> str:
        """Return text representation with ID for LLM context."""
        subject = self.subject or "(No subject)"
        sender = self.from_name or self.from_address
        date_str = f" | date: {self.occurred_at.date()}" if self.occurred_at else ""
        return f"Email (id: {self.id}): {subject} | from: {sender}{date_str}"

    # Thread relationship
    thread_id: UUID | None = Field(
        default=None,
        foreign_key="email_threads.id",
        index=True,
        description="The thread this email belongs to",
    )

    # Message identifiers
    message_id: str | None = Field(
        default=None,
        index=True,
        description="RFC 2822 Message-ID header",
    )
    in_reply_to: str | None = Field(
        default=None,
        description="RFC 2822 In-Reply-To header",
    )
    references: str | None = Field(
        default=None,
        description="RFC 2822 References header",
    )

    # Sender information
    sender_id: UUID | None = Field(
        default=None,
        foreign_key="people.id",
        index=True,
        description="Person who sent the email",
    )
    from_address: str = Field(
        description="From email address",
    )
    from_name: str | None = Field(
        default=None,
        description="From display name",
    )

    # Recipients
    to_addresses: str | None = Field(
        default=None,
        description="JSON-encoded list of To addresses",
    )
    cc_addresses: str | None = Field(
        default=None,
        description="JSON-encoded list of CC addresses",
    )
    bcc_addresses: str | None = Field(
        default=None,
        description="JSON-encoded list of BCC addresses",
    )
    reply_to_address: str | None = Field(
        default=None,
        description="Reply-To address if different from sender",
    )

    # Content
    subject: str | None = Field(
        default=None,
        description="Email subject line",
    )
    body_text: str | None = Field(
        default=None,
        description="Plain text body (stored for FTS)",
    )
    body_html: str | None = Field(
        default=None,
        description="HTML body if available",
    )
    snippet: str | None = Field(
        default=None,
        description="Short preview/snippet of the email",
    )

    # Email metadata
    folder: EmailFolder = enum_field(
        EmailFolder,
        default=EmailFolder.INBOX,
        description="Folder/label for the email (default: INBOX when unknown)",
    )
    labels: str | None = Field(
        default=None,
        description="JSON-encoded list of labels",
    )

    # Status flags
    is_read: bool = Field(
        default=False,
        description="Whether the email has been read",
    )
    is_starred: bool = Field(
        default=False,
        description="Whether the email is starred",
    )
    is_important: bool = Field(
        default=False,
        description="Whether the email is marked important",
    )
    is_draft: bool = Field(
        default=False,
        description="Whether this is a draft",
    )
    is_sent: bool = Field(
        default=False,
        description="Whether this was sent by the user",
    )
    is_spam: bool = Field(
        default=False,
        description="Whether marked as spam",
    )
    is_trash: bool = Field(
        default=False,
        description="Whether in trash",
    )

    # Attachments count
    attachment_count: int = Field(
        default=0,
        description="Number of attachments",
    )
    has_attachments: bool = Field(
        default=False,
        description="Whether email has attachments",
    )

    # Size
    size_bytes: int | None = Field(
        default=None,
        description="Email size in bytes",
    )

    # Embeddings for semantic search
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(TEXT_EMBEDDING_DIM)),
        description="Text embedding for text-to-text semantic search",
    )
    multimodal_embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(MULTIMODAL_EMBEDDING_DIM)),
        description="Multimodal embedding for text-to-image cross-modal search",
    )

    # Full-text search vector (auto-populated by database trigger)
    search_vector: str | None = Field(
        default=None,
        sa_column=Column(TSVECTOR),
        description="FTS vector for keyword search (auto-populated by trigger)",
    )

    # Relationships
    thread: "EmailThread" = Relationship(back_populates="emails")
    attachments: list["EmailAttachment"] = Relationship(back_populates="email")


class EmailAttachment(SimpleEntity, table=True):
    """Email attachment linking to Media model.

    Stores attachment metadata and links to the Media table for the actual file.
    """

    __tablename__ = "email_attachments"

    email_id: UUID = Field(
        foreign_key="emails.id",
        index=True,
        description="The email this attachment belongs to",
    )
    media_id: UUID | None = Field(
        default=None,
        foreign_key="media.id",
        description="Link to Media table for the file",
    )

    # Attachment metadata
    filename: str = Field(
        description="Original filename of the attachment",
    )
    mime_type: str | None = Field(
        default=None,
        description="MIME type of the attachment",
    )
    size_bytes: int | None = Field(
        default=None,
        description="Size of the attachment in bytes",
    )
    content_id: str | None = Field(
        default=None,
        description="Content-ID for inline attachments",
    )
    is_inline: bool = Field(
        default=False,
        description="Whether this is an inline attachment",
    )

    # Relationships
    email: Email = Relationship(back_populates="attachments")
