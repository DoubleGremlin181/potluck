"""Browsing history and bookmark models."""

from datetime import datetime
from typing import ClassVar
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import Field, Relationship

from potluck.core.constants import MULTIMODAL_EMBEDDING_DIM, TEXT_EMBEDDING_DIM
from potluck.models.base import BaseEntity, SimpleEntity, SourceType, TimestampedEntity


class BrowsingHistory(TimestampedEntity, table=True):
    """Browser history entry with URL, title, and visit time.

    Tracks web page visits from Chrome, Firefox, and other browsers.
    """

    __tablename__ = "browsing_history"

    # Search configuration - title is priority, url auto-discovered
    __searchable__: ClassVar[bool] = True
    __search_exclude_fields__: ClassVar[set[str]] = set()
    __search_priority_fields__: ClassVar[set[str]] = {"title"}
    __search_date_fields__: ClassVar[set[str]] = {"occurred_at"}

    def to_text_repr(self) -> str:
        """Return text representation with ID for LLM context."""
        title = self.title or self.url
        domain = self.domain or "unknown"
        date_str = f" | date: {self.occurred_at.date()}" if self.occurred_at else ""
        return f"BrowsingHistory (id: {self.id}): {title} | domain: {domain}{date_str}"

    # URL information
    url: str = Field(
        index=True,
        description="Full URL of the visited page",
    )
    url_hash: str | None = Field(
        default=None,
        index=True,
        description="Hash of URL for deduplication and fast lookup",
    )
    domain: str | None = Field(
        default=None,
        index=True,
        description="Domain of the URL (e.g., 'example.com')",
    )

    # Page metadata
    title: str | None = Field(
        default=None,
        description="Page title",
    )
    favicon_url: str | None = Field(
        default=None,
        description="URL to the page's favicon",
    )

    # Visit metadata
    visit_duration_seconds: int | None = Field(
        default=None,
        description="Time spent on the page in seconds",
    )

    # Transition/navigation type
    transition_type: str | None = Field(
        default=None,
        description="How the page was reached (link, typed, bookmark, etc.)",
    )
    referrer_url: str | None = Field(
        default=None,
        description="URL of the referring page",
    )

    # Browser information
    browser: str | None = Field(
        default=None,
        description="Browser name (Chrome, Firefox, etc.)",
    )
    device: str | None = Field(
        default=None,
        description="Device identifier if synced across devices",
    )

    # Search context
    search_query: str | None = Field(
        default=None,
        description="Search query if this was a search result",
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


class Bookmark(BaseEntity, table=True):
    """Saved bookmark with URL, title, and folder organization."""

    __tablename__ = "bookmarks"

    # Search configuration - title is priority, description auto-discovered
    __searchable__: ClassVar[bool] = True
    __search_exclude_fields__: ClassVar[set[str]] = set()
    __search_priority_fields__: ClassVar[set[str]] = {"title"}
    __search_date_fields__: ClassVar[set[str]] = {"created_at"}

    def to_text_repr(self) -> str:
        """Return text representation with ID for LLM context."""
        title = self.title or self.url
        folder = self.folder_path or "Bookmarks"
        return f"Bookmark (id: {self.id}): {title} | folder: {folder}"

    # URL information
    url: str = Field(
        index=True,
        description="Bookmarked URL",
    )
    url_hash: str | None = Field(
        default=None,
        index=True,
        description="Hash of URL for deduplication",
    )
    domain: str | None = Field(
        default=None,
        index=True,
        description="Domain of the URL",
    )

    # Bookmark metadata
    title: str | None = Field(
        default=None,
        description="Bookmark title",
    )
    description: str | None = Field(
        default=None,
        description="User description or notes",
    )
    favicon_url: str | None = Field(
        default=None,
        description="URL to the favicon",
    )
    icon_uri: str | None = Field(
        default=None,
        description="Data URI for favicon icon",
    )

    # Organization
    folder_id: UUID | None = Field(
        default=None,
        foreign_key="bookmark_folders.id",
        index=True,
        description="Folder containing this bookmark",
    )
    folder_path: str | None = Field(
        default=None,
        description="Full folder path (e.g., 'Bookmark Bar/Tech/Python')",
    )
    position: int | None = Field(
        default=None,
        description="Position within the folder",
    )

    # Timestamps from source
    bookmarked_at: datetime | None = Field(
        default=None,
        description="When the bookmark was originally created",
    )

    # Status
    is_favorite: bool = Field(
        default=False,
        description="Whether marked as favorite",
    )
    is_archived: bool = Field(
        default=False,
        description="Whether archived/hidden",
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

    # Note: tags field is inherited from BaseEntity

    # Relationships
    folder: "BookmarkFolder" = Relationship(back_populates="bookmarks")


class BookmarkFolder(SimpleEntity, table=True):
    """Folder for organizing bookmarks."""

    __tablename__ = "bookmark_folders"

    source_type: SourceType = Field(
        description="The source system this folder was imported from",
    )

    # Folder metadata
    name: str = Field(
        description="Folder name",
    )
    parent_id: UUID | None = Field(
        default=None,
        foreign_key="bookmark_folders.id",
        description="Parent folder ID for nesting",
    )
    full_path: str | None = Field(
        default=None,
        index=True,
        description="Full path from root (e.g., 'Bookmark Bar/Tech')",
    )
    position: int | None = Field(
        default=None,
        description="Position within parent folder",
    )

    # Source timestamps
    folder_created_at: datetime | None = Field(
        default=None,
        description="When the folder was created in the source",
    )
    folder_modified_at: datetime | None = Field(
        default=None,
        description="When the folder was last modified in the source",
    )

    # Relationships
    bookmarks: list[Bookmark] = Relationship(back_populates="folder")
