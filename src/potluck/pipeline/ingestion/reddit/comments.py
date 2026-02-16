"""Reddit comment ingestion from GDPR export."""

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.social import Platform, SocialComment
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.utils.hashing import compute_content_hash
from potluck.pipeline.utils.parsers import parse_datetime

logger = get_logger(__name__)


def ingest_comments(
    path: Path,
    saved_comment_ids: set[str],
    filters: PipelineFilter | None = None,
) -> Iterator[SocialComment]:
    """Ingest comments from Reddit GDPR export comments.csv.

    Args:
        path: Path to the extracted Reddit export directory.
        saved_comment_ids: Set of comment IDs that the user saved.
        filters: Optional date range filters.

    Yields:
        SocialComment entities.
    """
    comments_file = path / "comments.csv"
    if not comments_file.exists():
        logger.debug("No comments.csv found")
        return

    logger.info(f"Processing Reddit comments at {comments_file}")

    for row in _read_csv(comments_file):
        comment_id = row.get("id", "")
        if not comment_id:
            continue

        occurred_at = parse_datetime(row.get("date"))

        # Apply date filters
        if filters and occurred_at:
            if filters.since and occurred_at < filters.since:
                continue
            if filters.until and occurred_at >= filters.until:
                continue

        permalink = row.get("permalink", "")
        subreddit = row.get("subreddit", "")
        body = row.get("body")
        link = row.get("link", "")

        yield SocialComment(
            source_type=SourceType.REDDIT,
            source_id=comment_id,
            content_hash=compute_content_hash(f"reddit_comment:{comment_id}"),
            occurred_at=occurred_at,
            platform=Platform.REDDIT,
            comment_id=comment_id,
            permalink=permalink,
            is_own_comment=True,
            community_name=subreddit,
            body=body if body else None,
            post_title=link,
            is_saved=comment_id in saved_comment_ids,
        )


def _read_csv(path: Path) -> Iterator[dict[str, Any]]:
    """Read a Reddit CSV file using stdlib csv.DictReader."""
    with path.open(encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)
