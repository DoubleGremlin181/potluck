"""Browsing history and bookmark models."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from potluck.models.base import TimestampedEntity, _utc_now


class BrowsingHistory(TimestampedEntity, table=True):
    """Browser history entry with URL, title, and visit time.

    Tracks web page visits from Chrome, Firefox, and other browsers.
    """

    __tablename__ = "browsing_history"

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
    visit_count: int = Field(
        default=1,
        description="Number of times this URL was visited",
    )
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


class Bookmark(SQLModel, table=True):
    """Saved bookmark with URL, title, and folder organization."""

    __tablename__ = "bookmarks"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the bookmark",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the bookmark was created in the database",
    )
    updated_at: datetime = Field(
        default_factory=_utc_now,
        sa_column_kwargs={"onupdate": _utc_now},
        description="When the bookmark was last updated",
    )
    source_type: str = Field(
        description="Source of the bookmark (chrome, firefox, etc.)",
    )

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
    last_visited_at: datetime | None = Field(
        default=None,
        description="When the bookmark was last visited",
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

    # Tags for additional organization
    tags: str | None = Field(
        default=None,
        description="JSON-encoded list of tags",
    )

    # Relationships
    folder: "BookmarkFolder" = Relationship(back_populates="bookmarks")


class BookmarkFolder(SQLModel, table=True):
    """Folder for organizing bookmarks."""

    __tablename__ = "bookmark_folders"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the folder",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the folder was created",
    )
    source_type: str = Field(
        description="Source of the bookmark folder",
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
