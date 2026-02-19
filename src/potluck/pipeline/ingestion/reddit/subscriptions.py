"""Reddit subscription ingestion from GDPR export."""

from collections.abc import Iterator
from pathlib import Path

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.social import Platform, SocialFollow, SocialFollowType
from potluck.pipeline.utils.hashing import compute_content_hash
from potluck.pipeline.utils.parsers import parse_csv

logger = get_logger(__name__)


def ingest_subscriptions(path: Path) -> Iterator[SocialFollow]:
    """Ingest subscriptions from Reddit GDPR export subscribed_subreddits.csv.

    Args:
        path: Path to the extracted Reddit export directory.

    Yields:
        SocialFollow entities.
    """
    subs_file = path / "subscribed_subreddits.csv"
    if not subs_file.exists():
        logger.debug("No subscribed_subreddits.csv found")
        return

    logger.info(f"Processing Reddit subscriptions at {subs_file}")

    for row in parse_csv(subs_file, try_parse_dates=False):
        subreddit = str(row.get("subreddit", "")).strip()
        if not subreddit:
            continue

        yield SocialFollow(
            source_type=SourceType.REDDIT,
            source_id=f"reddit_sub:{subreddit}",
            content_hash=compute_content_hash(f"reddit_sub:{subreddit}"),
            platform=Platform.REDDIT,
            follow_type=SocialFollowType.SUBREDDIT,
            target_name=subreddit,
            target_url=f"https://reddit.com/r/{subreddit}",
        )
