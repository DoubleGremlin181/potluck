"""Email models for email data management."""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from potluck.models.base import TimestampedEntity, _utc_now


class EmailFolder(str, Enum):
    """Standard email folder types."""

    INBOX = "inbox"
    SENT = "sent"
    DRAFTS = "drafts"
    TRASH = "trash"
    SPAM = "spam"
    ARCHIVE = "archive"
    ALL_MAIL = "all_mail"
    STARRED = "starred"
    IMPORTANT = "important"
    CUSTOM = "custom"


class EmailThread(SQLModel, table=True):
    """Email conversation thread container.

    Groups related emails by conversation ID or subject threading.
    """

    __tablename__ = "email_threads"

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
        description="Email source (e.g., google_takeout, generic)",
    )
    source_id: str | None = Field(
        default=None,
        index=True,
        description="Thread ID from source (Gmail thread ID, etc.)",
    )

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
    folder: EmailFolder = Field(
        default=EmailFolder.ALL_MAIL,
        description="Folder/label for the email",
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

    # Relationships
    thread: "EmailThread" = Relationship(back_populates="emails")
    attachments: list["EmailAttachment"] = Relationship(back_populates="email")


class EmailAttachment(SQLModel, table=True):
    """Email attachment linking to Media model.

    Stores attachment metadata and links to the Media table for the actual file.
    """

    __tablename__ = "email_attachments"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the attachment",
    )
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

    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the attachment record was created",
    )

    # Relationships
    email: Email = Relationship(back_populates="attachments")
