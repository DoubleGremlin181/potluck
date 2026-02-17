"""Tests for Reddit GDPR export ingester."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from potluck.core.exceptions import IngestionError
from potluck.models.base import EntityType, SourceType
from potluck.models.social import Platform, PostType, SocialComment, SocialPost, Subscription
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.ingestion.reddit import RedditStage
from potluck.pipeline.ingestion.reddit.csv_utils import read_csv
from potluck.pipeline.utils.hashing import compute_content_hash

FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "reddit"


class TestRedditDetection:
    """Tests for RedditStage.detect()."""

    def test_detect_finds_all_entity_types(self) -> None:
        """Detection finds posts, comments, and subscriptions."""
        stage = RedditStage()
        result = stage.detect(FIXTURES_DIR)

        assert EntityType.SOCIAL_POST in result.entity_counts
        assert EntityType.SOCIAL_COMMENT in result.entity_counts
        assert EntityType.SUBSCRIPTION in result.entity_counts
        assert result.entity_counts[EntityType.SOCIAL_POST] == 4
        assert result.entity_counts[EntityType.SOCIAL_COMMENT] == 4
        assert result.entity_counts[EntityType.SUBSCRIPTION] == 5

    def test_detect_empty_directory(self, tmp_path: Path) -> None:
        """Detection returns empty counts for directory without CSVs."""
        stage = RedditStage()
        result = stage.detect(tmp_path)
        assert result.entity_counts == {}


class TestRedditPostIngestion:
    """Tests for Reddit post ingestion."""

    def test_ingest_posts(self) -> None:
        """Posts are ingested with correct field mapping."""
        stage = RedditStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.SOCIAL_POST}))

        posts = [e for e in entities if isinstance(e, SocialPost)]
        assert len(posts) == 4

        # Check first post
        first_post = next(p for p in posts if p.source_id == "t3_abc123")
        assert first_post.platform == Platform.REDDIT
        assert first_post.is_own_post is True
        assert first_post.community_name == "python"
        assert first_post.title == "My First Python Project"
        assert first_post.source_type == SourceType.REDDIT
        assert first_post.occurred_at is not None
        assert first_post.occurred_at.year == 2023

    def test_post_type_detection(self) -> None:
        """Link posts are distinguished from text posts."""
        stage = RedditStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.SOCIAL_POST}))
        posts = [e for e in entities if isinstance(e, SocialPost)]

        link_post = next(p for p in posts if p.source_id == "t3_def456")
        assert link_post.post_type == PostType.LINK
        assert link_post.link_url == "https://blog.example.com/rust-2024"

        text_post = next(p for p in posts if p.source_id == "t3_ghi789")
        assert text_post.post_type == PostType.TEXT

    def test_saved_posts_flagged(self) -> None:
        """Posts in saved_posts.csv are flagged with is_saved=True."""
        stage = RedditStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.SOCIAL_POST}))
        posts = [e for e in entities if isinstance(e, SocialPost)]

        saved_post = next(p for p in posts if p.source_id == "t3_saved1")
        assert saved_post.is_saved is True

        unsaved_post = next(p for p in posts if p.source_id == "t3_abc123")
        assert unsaved_post.is_saved is False

    def test_content_hash_deterministic(self) -> None:
        """Content hashes are deterministic for the same post."""
        stage = RedditStage()
        entities1 = list(stage.execute(FIXTURES_DIR, {EntityType.SOCIAL_POST}))
        entities2 = list(stage.execute(FIXTURES_DIR, {EntityType.SOCIAL_POST}))

        posts1 = sorted(
            [e for e in entities1 if isinstance(e, SocialPost)], key=lambda p: p.source_id or ""
        )
        posts2 = sorted(
            [e for e in entities2 if isinstance(e, SocialPost)], key=lambda p: p.source_id or ""
        )
        for p1, p2 in zip(posts1, posts2, strict=True):
            assert p1.content_hash == p2.content_hash


class TestRedditCommentIngestion:
    """Tests for Reddit comment ingestion."""

    def test_ingest_comments(self) -> None:
        """Comments are ingested with correct field mapping."""
        stage = RedditStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.SOCIAL_COMMENT}))

        comments = [e for e in entities if isinstance(e, SocialComment)]
        assert len(comments) == 4

        first_comment = next(c for c in comments if c.source_id == "t1_comm01")
        assert first_comment.platform == Platform.REDDIT
        assert first_comment.is_own_comment is True
        assert first_comment.community_name == "python"
        assert first_comment.body is not None
        assert "feedback" in first_comment.body

    def test_saved_comments_flagged(self) -> None:
        """Comments in saved_comments.csv are flagged with is_saved=True."""
        stage = RedditStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.SOCIAL_COMMENT}))
        comments = [e for e in entities if isinstance(e, SocialComment)]

        saved = next(c for c in comments if c.source_id == "t1_saved2")
        assert saved.is_saved is True

        unsaved = next(c for c in comments if c.source_id == "t1_comm01")
        assert unsaved.is_saved is False


class TestRedditSubscriptionIngestion:
    """Tests for Reddit subscription ingestion."""

    def test_ingest_subscriptions(self) -> None:
        """Subscriptions are ingested correctly."""
        stage = RedditStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.SUBSCRIPTION}))

        subs = [e for e in entities if isinstance(e, Subscription)]
        assert len(subs) == 5

        python_sub = next(s for s in subs if s.target_name == "python")
        assert python_sub.platform == Platform.REDDIT
        assert python_sub.target_url == "https://reddit.com/r/python"
        assert python_sub.source_type == SourceType.REDDIT


class TestRedditDateFiltering:
    """Tests for date range filtering."""

    def test_since_filter(self) -> None:
        """Posts before 'since' date are excluded."""
        stage = RedditStage()
        since = datetime(2024, 1, 1, tzinfo=UTC)
        entities = list(
            stage.execute(FIXTURES_DIR, {EntityType.SOCIAL_POST}, PipelineFilter(since=since))
        )

        posts = [e for e in entities if isinstance(e, SocialPost)]
        # The 2023 post should be excluded
        assert all(p.occurred_at is not None and p.occurred_at >= since for p in posts)
        assert len(posts) == 3

    def test_until_filter(self) -> None:
        """Posts after 'until' date are excluded."""
        stage = RedditStage()
        until = datetime(2024, 2, 1, tzinfo=UTC)
        entities = list(
            stage.execute(FIXTURES_DIR, {EntityType.SOCIAL_POST}, PipelineFilter(until=until))
        )

        posts = [e for e in entities if isinstance(e, SocialPost)]
        assert all(p.occurred_at is not None and p.occurred_at < until for p in posts)


class TestRedditEntityTypeFiltering:
    """Tests for entity type selection."""

    def test_only_posts(self) -> None:
        """Only posts are returned when only SOCIAL_POST requested."""
        stage = RedditStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.SOCIAL_POST}))
        assert all(isinstance(e, SocialPost) for e in entities)

    def test_only_subscriptions(self) -> None:
        """Only subscriptions are returned when only SUBSCRIPTION requested."""
        stage = RedditStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.SUBSCRIPTION}))
        assert all(isinstance(e, Subscription) for e in entities)

    @pytest.mark.parametrize(
        "entity_type",
        [EntityType.SOCIAL_POST, EntityType.SOCIAL_COMMENT, EntityType.SUBSCRIPTION],
    )
    def test_individual_entity_types(self, entity_type: EntityType) -> None:
        """Each supported entity type can be ingested independently."""
        stage = RedditStage()
        entities = list(stage.execute(FIXTURES_DIR, {entity_type}))
        assert len(entities) > 0


class TestRedditCsvUtilsIngestionError:
    """Tests for csv_utils IngestionError wrapping."""

    def test_missing_csv_raises_ingestion_error(self, tmp_path: Path) -> None:
        """Non-existent CSV raises IngestionError."""
        missing = tmp_path / "nonexistent.csv"
        with pytest.raises(IngestionError, match="Failed to read CSV"):
            list(read_csv(missing))

    def test_invalid_encoding_raises_ingestion_error(self, tmp_path: Path) -> None:
        """CSV with invalid encoding raises IngestionError."""
        bad_file = tmp_path / "bad.csv"
        # Write binary content that is not valid UTF-8
        bad_file.write_bytes(b"header\n\x80\x81\x82\n")
        with pytest.raises(IngestionError, match="Failed to read CSV"):
            list(read_csv(bad_file))


class TestRedditSubscriptionSourceIdAndContentHash:
    """Tests for subscription source_id and content_hash fields."""

    def test_subscription_source_id_format(self) -> None:
        """Subscription source_id follows 'reddit_sub:SUBREDDIT' pattern."""
        stage = RedditStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.SUBSCRIPTION}))
        subs = [e for e in entities if isinstance(e, Subscription)]

        for sub in subs:
            assert sub.source_id is not None
            assert sub.source_id.startswith("reddit_sub:")
            subreddit_name = sub.source_id.split(":", 1)[1]
            assert subreddit_name == sub.target_name

    def test_subscription_content_hash_uses_compute_content_hash(self) -> None:
        """Subscription content_hash is computed from the source_id via compute_content_hash."""
        stage = RedditStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.SUBSCRIPTION}))
        subs = [e for e in entities if isinstance(e, Subscription)]

        for sub in subs:
            expected_hash = compute_content_hash(f"reddit_sub:{sub.target_name}")
            assert sub.content_hash == expected_hash

    def test_subscription_content_hash_deterministic(self) -> None:
        """Subscription content hashes are deterministic across runs."""
        stage = RedditStage()
        entities1 = list(stage.execute(FIXTURES_DIR, {EntityType.SUBSCRIPTION}))
        entities2 = list(stage.execute(FIXTURES_DIR, {EntityType.SUBSCRIPTION}))

        subs1 = sorted(
            [e for e in entities1 if isinstance(e, Subscription)],
            key=lambda s: s.source_id or "",
        )
        subs2 = sorted(
            [e for e in entities2 if isinstance(e, Subscription)],
            key=lambda s: s.source_id or "",
        )
        for s1, s2 in zip(subs1, subs2, strict=True):
            assert s1.content_hash == s2.content_hash
