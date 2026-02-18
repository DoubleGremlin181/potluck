"""Reddit comment ingestion from GDPR export."""

from collections.abc import Iterator
from pathlib import Path

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.social import Platform, SocialComment
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.utils.hashing import compute_content_hash
from potluck.pipeline.utils.parsers import parse_csv

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

    for row in parse_csv(comments_file, date_columns=["date"], try_parse_dates=False):
        comment_id = str(row.get("id", ""))
        if not comment_id:
            continue

        occurred_at = row.get("date")

        # Apply date filters
        if filters and occurred_at:
            if filters.since and occurred_at < filters.since:
                continue
            if filters.until and occurred_at >= filters.until:
                continue

        permalink = str(row.get("permalink", ""))
        subreddit = str(row.get("subreddit", ""))
        body = row.get("body")

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
            is_saved=comment_id in saved_comment_ids,
        )
