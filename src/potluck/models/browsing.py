"""Browsing history and bookmark models."""

from datetime import datetime
from uuid import UUID

from sqlmodel import Field, Relationship

from potluck.models.base import BaseEntity, SourceTrackedEntity, TimestampedEntity


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


class Bookmark(BaseEntity, table=True):
    """Saved bookmark with URL, title, and folder organization."""

    __tablename__ = "bookmarks"

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

    # Note: tags field is inherited from BaseEntity

    # Relationships
    folder: "BookmarkFolder" = Relationship(back_populates="bookmarks")


class BookmarkFolder(SourceTrackedEntity, table=True):
    """Folder for organizing bookmarks."""

    __tablename__ = "bookmark_folders"

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
