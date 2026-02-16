"""Reddit post ingestion from GDPR export."""

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.social import Platform, PostType, SocialPost
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.utils.hashing import compute_content_hash
from potluck.pipeline.utils.parsers import parse_datetime

logger = get_logger(__name__)


def ingest_posts(
    path: Path,
    saved_post_ids: set[str],
    filters: PipelineFilter | None = None,
) -> Iterator[SocialPost]:
    """Ingest posts from Reddit GDPR export posts.csv.

    Args:
        path: Path to the extracted Reddit export directory.
        saved_post_ids: Set of post IDs that the user saved.
        filters: Optional date range filters.

    Yields:
        SocialPost entities.
    """
    posts_file = path / "posts.csv"
    if not posts_file.exists():
        logger.debug("No posts.csv found")
        return

    logger.info(f"Processing Reddit posts at {posts_file}")

    for row in _read_csv(posts_file):
        post_id = row.get("id", "")
        if not post_id:
            continue

        occurred_at = parse_datetime(row.get("date"))

        # Apply date filters
        if filters and occurred_at:
            if filters.since and occurred_at < filters.since:
                continue
            if filters.until and occurred_at >= filters.until:
                continue

        permalink = row.get("permalink", "")
        url = row.get("url", "")
        subreddit = row.get("subreddit", "")
        title = row.get("title", "")
        body = row.get("body")

        # Determine post type: if URL differs from permalink, it's a link post
        if (
            url
            and permalink
            and url != f"https://reddit.com{permalink}"
            and not url.startswith("https://reddit.com/r/")
        ):
            post_type = PostType.LINK
        else:
            post_type = PostType.TEXT

        yield SocialPost(
            source_type=SourceType.REDDIT,
            source_id=post_id,
            content_hash=compute_content_hash(f"reddit_post:{post_id}"),
            occurred_at=occurred_at,
            platform=Platform.REDDIT,
            post_type=post_type,
            post_id=post_id,
            permalink=permalink,
            url=url if post_type == PostType.LINK else None,
            link_url=url if post_type == PostType.LINK else None,
            is_own_post=True,
            community_name=subreddit,
            title=title,
            body=body if body else None,
            is_saved=post_id in saved_post_ids,
        )


def _read_csv(path: Path) -> Iterator[dict[str, Any]]:
    """Read a Reddit CSV file using stdlib csv.DictReader.

    We use stdlib csv instead of Polars parse_csv because Reddit's date
    format ('2023-06-15 14:30:00 UTC') confuses Polars' type inference.
    """
    with path.open(encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)
