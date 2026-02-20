"""Social media models for platforms like Reddit, YouTube, etc."""

from datetime import datetime
from enum import Enum
from typing import ClassVar
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import Field, Relationship

from potluck.core.constants import MULTIMODAL_EMBEDDING_DIM, TEXT_EMBEDDING_DIM
from potluck.models.base import BaseEntity, TimestampedEntity, enum_field


class Platform(str, Enum):
    """Social media platform types."""

    REDDIT = "reddit"
    YOUTUBE = "youtube"
    TWITTER = "twitter"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"
    TIKTOK = "tiktok"
    OTHER = "other"


class PostType(str, Enum):
    """Type of social post."""

    TEXT = "text"
    LINK = "link"
    IMAGE = "image"
    VIDEO = "video"
    POLL = "poll"
    CROSSPOST = "crosspost"
    OTHER = "other"


class SocialFollowType(str, Enum):
    """Type of social follow."""

    SUBREDDIT = "subreddit"
    USER = "user"
    CHANNEL = "channel"
    PAGE = "page"
    HASHTAG = "hashtag"
    TOPIC = "topic"
    OTHER = "other"


class SocialPost(TimestampedEntity, table=True):
    """Post from social media platforms like Reddit.

    Source-agnostic model that works for Reddit posts, YouTube videos, tweets, etc.
    """

    __tablename__ = "social_posts"

    # Search configuration - title is priority, body auto-discovered
    __searchable__: ClassVar[bool] = True
    __search_exclude_fields__: ClassVar[set[str]] = set()
    __search_priority_fields__: ClassVar[set[str]] = {"title"}
    __search_date_fields__: ClassVar[set[str]] = {"occurred_at"}

    def to_text_repr(self) -> str:
        """Return text representation with ID for LLM context."""
        title = self.title or "(Untitled)"
        community = f"r/{self.community_name}" if self.community_name else self.platform.value
        date_str = f" | date: {self.occurred_at.date()}" if self.occurred_at else ""
        return f"SocialPost (id: {self.id}): {title} | {community}{date_str}"

    # Platform information
    platform: Platform = Field(
        description="Social media platform",
    )
    post_type: PostType = enum_field(
        default=PostType.TEXT,
        description="Type of post content",
    )

    # Post identifiers
    post_id: str | None = Field(
        default=None,
        index=True,
        description="Platform-specific post ID",
    )
    url: str | None = Field(
        default=None,
        description="URL to the post",
    )
    permalink: str | None = Field(
        default=None,
        description="Permanent link to the post",
    )

    # Author information
    author_id: UUID | None = Field(
        default=None,
        foreign_key="people.id",
        index=True,
        description="Person who created the post",
    )
    author_name: str | None = Field(
        default=None,
        description="Author username/handle",
    )
    is_own_post: bool = Field(
        default=False,
        description="Whether this post was created by the data owner",
    )

    # Community/destination
    community_name: str | None = Field(
        default=None,
        index=True,
        description="Subreddit, channel, or community name",
    )
    community_id: str | None = Field(
        default=None,
        description="Platform-specific community ID",
    )

    # Content
    title: str | None = Field(
        default=None,
        description="Post title (for Reddit, YouTube)",
    )
    body: str | None = Field(
        default=None,
        description="Post body text (stored for FTS)",
    )
    body_html: str | None = Field(
        default=None,
        description="HTML body if available",
    )

    # External content
    link_url: str | None = Field(
        default=None,
        description="External URL for link posts",
    )
    link_domain: str | None = Field(
        default=None,
        description="Domain of external link",
    )

    # Media
    media_id: UUID | None = Field(
        default=None,
        foreign_key="media.id",
        description="Associated media file",
    )
    thumbnail_url: str | None = Field(
        default=None,
        description="URL to thumbnail image",
    )
    media_urls: str | None = Field(
        default=None,
        description="JSON-encoded list of media URLs",
    )

    # Engagement metrics
    score: int | None = Field(
        default=None,
        description="Score/upvotes/likes count",
    )
    upvotes: int | None = Field(
        default=None,
        description="Upvote count (Reddit)",
    )
    downvotes: int | None = Field(
        default=None,
        description="Downvote count (Reddit)",
    )
    comment_count: int | None = Field(
        default=None,
        description="Number of comments",
    )
    view_count: int | None = Field(
        default=None,
        description="View count (YouTube)",
    )
    share_count: int | None = Field(
        default=None,
        description="Share/repost count",
    )

    # Post metadata
    is_nsfw: bool = Field(
        default=False,
        description="Whether post is marked NSFW",
    )
    is_spoiler: bool = Field(
        default=False,
        description="Whether post is marked as spoiler",
    )
    is_pinned: bool = Field(
        default=False,
        description="Whether post is pinned",
    )
    is_locked: bool = Field(
        default=False,
        description="Whether comments are locked",
    )
    is_archived: bool = Field(
        default=False,
        description="Whether post is archived",
    )
    is_deleted: bool = Field(
        default=False,
        description="Whether post was deleted",
    )

    # User interaction (for saved/liked posts)
    is_saved: bool = Field(
        default=False,
        description="Whether the data owner saved this post",
    )
    is_liked: bool = Field(
        default=False,
        description="Whether the data owner liked/upvoted this",
    )
    saved_at: datetime | None = Field(
        default=None,
        description="When the post was saved",
    )

    # Flair/tags
    flair: str | None = Field(
        default=None,
        description="Post flair/category",
    )
    tags: str | None = Field(
        default=None,
        description="JSON-encoded list of tags",
    )

    # Crosspost reference
    crosspost_parent_id: str | None = Field(
        default=None,
        description="ID of original post if crossposted",
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
    comments: list["SocialComment"] = Relationship(back_populates="post")


class SocialComment(TimestampedEntity, table=True):
    """Comment on a social media post."""

    __tablename__ = "social_comments"

    # Search configuration - body auto-discovered
    __searchable__: ClassVar[bool] = True
    __search_exclude_fields__: ClassVar[set[str]] = set()
    __search_priority_fields__: ClassVar[set[str]] = set()
    __search_date_fields__: ClassVar[set[str]] = {"occurred_at"}

    def to_text_repr(self) -> str:
        """Return text representation with ID for LLM context."""
        author = self.author_name or "Unknown"
        body_preview = (self.body or "")[:60]
        if len(self.body or "") > 60:
            body_preview += "..."
        context = self.post_title or self.community_name or ""
        post_ref = f" | post: {self.post_id}" if self.post_id else ""
        return f"SocialComment (id: {self.id}): {author} on {context}: {body_preview}{post_ref}"

    # Post relationship
    post_id: UUID | None = Field(
        default=None,
        foreign_key="social_posts.id",
        index=True,
        description="The post this comment belongs to",
    )

    # Comment identifiers
    platform: Platform = Field(
        description="Social media platform",
    )
    comment_id: str | None = Field(
        default=None,
        index=True,
        description="Platform-specific comment ID",
    )
    permalink: str | None = Field(
        default=None,
        description="Permanent link to the comment",
    )

    # Author information
    author_id: UUID | None = Field(
        default=None,
        foreign_key="people.id",
        index=True,
        description="Person who wrote the comment",
    )
    author_name: str | None = Field(
        default=None,
        description="Author username/handle",
    )
    is_own_comment: bool = Field(
        default=False,
        description="Whether written by the data owner",
    )

    # Thread/reply structure
    parent_comment_id: UUID | None = Field(
        default=None,
        foreign_key="social_comments.id",
        description="Parent comment if this is a reply",
    )
    depth: int = Field(
        default=0,
        description="Nesting depth in the comment tree",
    )

    # Content
    body: str | None = Field(
        default=None,
        description="Comment text (stored for FTS)",
    )
    body_html: str | None = Field(
        default=None,
        description="HTML body if available",
    )

    # Context (for when post isn't imported)
    post_title: str | None = Field(
        default=None,
        description="Title of the post being commented on",
    )
    community_name: str | None = Field(
        default=None,
        description="Community where comment was made",
    )

    # Engagement metrics
    score: int | None = Field(
        default=None,
        description="Score/upvotes/likes count",
    )
    upvotes: int | None = Field(
        default=None,
        description="Upvote count",
    )
    downvotes: int | None = Field(
        default=None,
        description="Downvote count",
    )

    # Status
    is_edited: bool = Field(
        default=False,
        description="Whether comment was edited",
    )
    edited_at: datetime | None = Field(
        default=None,
        description="When comment was edited",
    )
    is_deleted: bool = Field(
        default=False,
        description="Whether comment was deleted",
    )
    is_stickied: bool = Field(
        default=False,
        description="Whether comment is stickied",
    )

    # User interaction
    is_saved: bool = Field(
        default=False,
        description="Whether saved by data owner",
    )
    is_liked: bool = Field(
        default=False,
        description="Whether liked by data owner",
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
    post: "SocialPost" = Relationship(back_populates="comments")


class SocialFollow(BaseEntity, table=True):
    """Follow/subscription to subreddits, channels, users, etc."""

    __tablename__ = "social_follows"

    # Platform information
    platform: Platform = Field(
        description="Social media platform",
    )
    follow_type: SocialFollowType = enum_field(
        description="Type of social follow",
    )

    # Follow target
    target_id: str | None = Field(
        default=None,
        index=True,
        description="Platform-specific ID of the follow target",
    )
    target_name: str = Field(
        index=True,
        description="Name of what's being followed",
    )
    target_url: str | None = Field(
        default=None,
        description="URL to the follow target",
    )
    target_description: str | None = Field(
        default=None,
        description="Description of the target",
    )

    # Follow metadata
    followed_at: datetime | None = Field(
        default=None,
        description="When the follow started",
    )
    unfollowed_at: datetime | None = Field(
        default=None,
        description="When unfollowed (if applicable)",
    )
    is_active: bool = Field(
        default=True,
        description="Whether currently following",
    )
