"""Tests for SocialPost, SocialComment, and Subscription models."""

from potluck.models.base import SourceType
from potluck.models.social import (
    Platform,
    PostType,
    SocialComment,
    SocialPost,
    Subscription,
    SubscriptionType,
)


class TestSocialModels:
    """Tests for SocialPost, SocialComment, and Subscription models."""

    def test_social_post_creation(self) -> None:
        """SocialPost can be created."""
        post = SocialPost(
            source_type=SourceType.REDDIT,
            platform=Platform.REDDIT,
            title="My Post",
        )
        assert post.platform == Platform.REDDIT
        assert post.post_type == PostType.TEXT
        assert post.is_nsfw is False
        assert post.is_own_post is False

    def test_platform_enum(self) -> None:
        """Platform enum has expected values."""
        expected = {
            "reddit",
            "youtube",
            "twitter",
            "facebook",
            "instagram",
            "linkedin",
            "tiktok",
            "other",
        }
        actual = {p.value for p in Platform}
        assert actual == expected

    def test_post_type_enum(self) -> None:
        """PostType enum has expected values."""
        expected = {"text", "link", "image", "video", "poll", "crosspost", "other"}
        actual = {t.value for t in PostType}
        assert actual == expected

    def test_social_comment_creation(self) -> None:
        """SocialComment can be created."""
        comment = SocialComment(
            source_type=SourceType.REDDIT,
            platform=Platform.REDDIT,
            body="Great post!",
        )
        assert comment.body == "Great post!"
        assert comment.depth == 0
        assert comment.is_own_comment is False

    def test_subscription_creation(self) -> None:
        """Subscription can be created."""
        sub = Subscription(
            source_type=SourceType.GOOGLE_TAKEOUT,
            platform=Platform.YOUTUBE,
            subscription_type=SubscriptionType.CHANNEL,
            target_name="Tech Channel",
        )
        assert sub.platform == Platform.YOUTUBE
        assert sub.target_name == "Tech Channel"
        assert sub.is_active is True

    def test_subscription_type_enum(self) -> None:
        """SubscriptionType enum has expected values."""
        expected = {"subreddit", "user", "channel", "page", "hashtag", "topic", "other"}
        actual = {t.value for t in SubscriptionType}
        assert actual == expected
