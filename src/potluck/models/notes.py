"""Knowledge notes model for personal notes and documents."""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from potluck.models.base import TimestampedEntity, _utc_now


class NoteType(str, Enum):
    """Type of knowledge note."""

    NOTE = "note"
    TASK = "task"
    JOURNAL = "journal"
    DOCUMENT = "document"
    SNIPPET = "snippet"
    QUOTE = "quote"
    IDEA = "idea"
    REFERENCE = "reference"
    OTHER = "other"


class KnowledgeNote(TimestampedEntity, table=True):
    """Personal knowledge note with content, keywords, and embeddings.

    Stores notes from Google Keep, Notion exports, markdown files, etc.
    """

    __tablename__ = "knowledge_notes"

    # Note metadata
    note_type: NoteType = Field(
        default=NoteType.NOTE,
        description="Type of note",
    )
    title: str | None = Field(
        default=None,
        description="Note title",
    )

    # Content
    content: str | None = Field(
        default=None,
        description="Note content (stored for FTS)",
    )
    content_html: str | None = Field(
        default=None,
        description="HTML formatted content",
    )
    content_markdown: str | None = Field(
        default=None,
        description="Markdown formatted content",
    )

    # Task-specific fields (for Google Tasks, Keep tasks)
    is_task: bool = Field(
        default=False,
        description="Whether this is a task/todo item",
    )
    is_completed: bool = Field(
        default=False,
        description="Whether the task is completed",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="When the task was completed",
    )
    due_at: datetime | None = Field(
        default=None,
        description="When the task is due",
    )
    priority: int | None = Field(
        default=None,
        description="Task priority (higher = more important)",
    )

    # Organization
    folder: str | None = Field(
        default=None,
        description="Folder or notebook name",
    )
    labels: str | None = Field(
        default=None,
        description="JSON-encoded list of labels",
    )
    keywords: str | None = Field(
        default=None,
        description="JSON-encoded list of extracted keywords",
    )

    # Status
    is_pinned: bool = Field(
        default=False,
        description="Whether the note is pinned",
    )
    is_archived: bool = Field(
        default=False,
        description="Whether the note is archived",
    )
    is_trashed: bool = Field(
        default=False,
        description="Whether the note is in trash",
    )

    # Linked content
    url: str | None = Field(
        default=None,
        description="Associated URL if this is a web clip",
    )
    linked_media_ids: str | None = Field(
        default=None,
        description="JSON-encoded list of attached media IDs",
    )

    # Reminders
    reminder_at: datetime | None = Field(
        default=None,
        description="Reminder datetime",
    )
    has_reminder: bool = Field(
        default=False,
        description="Whether a reminder is set",
    )

    # Embedding for semantic search
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(768)),
        description="Text embedding vector for semantic search",
    )

    # Color (Keep notes have colors)
    color: str | None = Field(
        default=None,
        description="Note color (e.g., 'yellow', 'blue')",
    )


class NoteChecklist(SQLModel, table=True):
    """Checklist item within a note (for Keep checklists)."""

    __tablename__ = "note_checklists"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the checklist item",
    )
    note_id: UUID = Field(
        foreign_key="knowledge_notes.id",
        index=True,
        description="The note this checklist belongs to",
    )
    content: str = Field(
        description="Checklist item text",
    )
    is_checked: bool = Field(
        default=False,
        description="Whether the item is checked off",
    )
    position: int = Field(
        default=0,
        description="Position in the checklist",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the item was created",
    )
