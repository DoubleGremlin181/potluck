"""Tests for SocialPost, SocialComment, and SocialFollow models."""

from potluck.models.base import SourceType
from potluck.models.social import (
    Platform,
    PostType,
    SocialComment,
    SocialFollow,
    SocialFollowType,
    SocialPost,
)


class TestSocialModels:
    """Tests for SocialPost, SocialComment, and SocialFollow models."""

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

    def test_social_follow_creation(self) -> None:
        """SocialFollow can be created."""
        follow = SocialFollow(
            source_type=SourceType.GOOGLE_TAKEOUT,
            platform=Platform.YOUTUBE,
            follow_type=SocialFollowType.CHANNEL,
            target_name="Tech Channel",
        )
        assert follow.platform == Platform.YOUTUBE
        assert follow.target_name == "Tech Channel"
        assert follow.is_active is True

    def test_social_follow_type_enum(self) -> None:
        """SocialFollowType enum has expected values."""
        expected = {"subreddit", "user", "channel", "page", "hashtag", "topic", "other"}
        actual = {t.value for t in SocialFollowType}
        assert actual == expected
