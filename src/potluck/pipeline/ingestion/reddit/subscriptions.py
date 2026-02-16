"""Reddit subscription ingestion from GDPR export."""

import csv
from collections.abc import Iterator
from pathlib import Path

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.social import Platform, Subscription, SubscriptionType

logger = get_logger(__name__)


def ingest_subscriptions(path: Path) -> Iterator[Subscription]:
    """Ingest subscriptions from Reddit GDPR export subscribed_subreddits.csv.

    Args:
        path: Path to the extracted Reddit export directory.

    Yields:
        Subscription entities.
    """
    subs_file = path / "subscribed_subreddits.csv"
    if not subs_file.exists():
        logger.debug("No subscribed_subreddits.csv found")
        return

    logger.info(f"Processing Reddit subscriptions at {subs_file}")

    with subs_file.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            subreddit = row.get("subreddit", "").strip()
            if not subreddit:
                continue

            yield Subscription(
                source_type=SourceType.REDDIT,
                platform=Platform.REDDIT,
                subscription_type=SubscriptionType.SUBREDDIT,
                target_name=subreddit,
                target_url=f"https://reddit.com/r/{subreddit}",
            )
