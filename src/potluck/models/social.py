"""Social media models for platforms like Reddit, YouTube, etc."""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from potluck.models.base import SourceType, TimestampedEntity
from potluck.models.utils import utc_now


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


class SubscriptionType(str, Enum):
    """Type of subscription/follow."""

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

    # Platform information
    platform: Platform = Field(
        description="Social media platform",
    )
    post_type: PostType = Field(
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

    # Relationships
    comments: list["SocialComment"] = Relationship(back_populates="post")


class SocialComment(TimestampedEntity, table=True):
    """Comment on a social media post."""

    __tablename__ = "social_comments"

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

    # Relationships
    post: "SocialPost" = Relationship(back_populates="comments")


class Subscription(SQLModel, table=True):
    """Subscription to subreddits, channels, users, etc."""

    __tablename__ = "subscriptions"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the subscription",
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        description="When the subscription was recorded",
    )
    source_type: SourceType = Field(
        description="The source system this subscription was imported from",
    )

    # Platform information
    platform: Platform = Field(
        description="Social media platform",
    )
    subscription_type: SubscriptionType = Field(
        description="Type of subscription",
    )

    # Subscription target
    target_id: str | None = Field(
        default=None,
        index=True,
        description="Platform-specific ID of the subscription target",
    )
    target_name: str = Field(
        index=True,
        description="Name of what's being subscribed to",
    )
    target_url: str | None = Field(
        default=None,
        description="URL to the subscription target",
    )
    target_description: str | None = Field(
        default=None,
        description="Description of the target",
    )

    # Subscription metadata
    subscribed_at: datetime | None = Field(
        default=None,
        description="When the subscription started",
    )
    unsubscribed_at: datetime | None = Field(
        default=None,
        description="When unsubscribed (if applicable)",
    )
    is_active: bool = Field(
        default=True,
        description="Whether currently subscribed",
    )

    # Target statistics (at time of export)
    subscriber_count: int | None = Field(
        default=None,
        description="Number of subscribers the target has",
    )
    post_count: int | None = Field(
        default=None,
        description="Number of posts in the community",
    )

    # Notification preferences
    notifications_enabled: bool = Field(
        default=True,
        description="Whether notifications are enabled",
    )
