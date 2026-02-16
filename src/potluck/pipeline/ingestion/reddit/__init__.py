"""Reddit GDPR export ingestion stage.

Handles importing data from Reddit's GDPR data export archives including:
- Posts (posts.csv)
- Comments (comments.csv)
- Subscriptions (subscribed_subreddits.csv)
- Saved posts/comments (saved_posts.csv, saved_comments.csv) — merged as flags
"""

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from potluck.core.logging import get_logger
from potluck.models.base import EntityType, IngestableEntity, SourceType
from potluck.pipeline.dtos import DetectionResult, PipelineFilter
from potluck.pipeline.ingestion.base import BaseIngestionStage
from potluck.pipeline.ingestion.reddit.comments import ingest_comments
from potluck.pipeline.ingestion.reddit.posts import ingest_posts
from potluck.pipeline.ingestion.reddit.subscriptions import ingest_subscriptions
from potluck.pipeline.ingestion.registry import register
from potluck.pipeline.utils.archive import extracted

logger = get_logger(__name__)


@register
class RedditStage(BaseIngestionStage):
    """Ingestion stage for Reddit GDPR export archives.

    Handles posts, comments, and subscriptions from Reddit data exports.
    Supports both ZIP archives and extracted directories.
    """

    SOURCE_TYPE: ClassVar[SourceType] = SourceType.REDDIT

    FILENAME_PATTERNS: ClassVar[list[str]] = [
        r"export_\w+_\d{8}.*\.zip",  # export_doublegremlin181_20251205.zip
        r"reddit_data_\d{4}-\d{2}-\d{2}.*\.zip",
        r"reddit_data",
    ]

    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {
        EntityType.SOCIAL_POST,
        EntityType.SOCIAL_COMMENT,
        EntityType.SUBSCRIPTION,
    }

    def detect(self, path: Path) -> DetectionResult:
        """Scan the Reddit export and return available entity types with counts."""
        with extracted(path) as content_path:
            return self._detect_from_path(content_path)

    def _detect_from_path(self, path: Path) -> DetectionResult:
        entity_counts: dict[EntityType, int] = {}
        metadata: dict[str, str] = {}

        posts_count = _count_csv_rows(path / "posts.csv")
        if posts_count > 0:
            entity_counts[EntityType.SOCIAL_POST] = posts_count

        comments_count = _count_csv_rows(path / "comments.csv")
        if comments_count > 0:
            entity_counts[EntityType.SOCIAL_COMMENT] = comments_count

        subs_count = _count_csv_rows(path / "subscribed_subreddits.csv")
        if subs_count > 0:
            entity_counts[EntityType.SUBSCRIPTION] = subs_count

        if entity_counts:
            metadata["source"] = "Reddit GDPR Export"

        return DetectionResult(entity_counts=entity_counts, metadata=metadata)

    def execute(
        self,
        path: Path,
        entity_types: set[EntityType] | None = None,
        filters: PipelineFilter | None = None,
    ) -> Iterator[IngestableEntity]:
        """Yield entities from the Reddit GDPR export."""
        with extracted(path) as content_path:
            yield from self._execute_from_path(content_path, entity_types, filters)

    def _execute_from_path(
        self,
        path: Path,
        entity_types: set[EntityType] | None = None,
        filters: PipelineFilter | None = None,
    ) -> Iterator[IngestableEntity]:
        types_to_process = (
            entity_types or self.SUPPORTED_ENTITY_TYPES
        ) & self.SUPPORTED_ENTITY_TYPES

        logger.info(f"Processing Reddit export at {path} for types: {types_to_process}")

        # Load saved IDs for cross-referencing
        saved_post_ids = _load_saved_ids(path / "saved_posts.csv")
        saved_comment_ids = _load_saved_ids(path / "saved_comments.csv")

        if EntityType.SOCIAL_POST in types_to_process:
            yield from ingest_posts(path, saved_post_ids, filters)

        if EntityType.SOCIAL_COMMENT in types_to_process:
            yield from ingest_comments(path, saved_comment_ids, filters)

        if EntityType.SUBSCRIPTION in types_to_process:
            yield from ingest_subscriptions(path)


def _count_csv_rows(csv_path: Path) -> int:
    """Count data rows in a CSV file (excluding header)."""
    if not csv_path.exists():
        return 0
    try:
        with csv_path.open(encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # Skip header
            return sum(1 for _ in reader)
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Failed to count rows in {csv_path}: {e}")
        return 0


def _load_saved_ids(csv_path: Path) -> set[str]:
    """Load saved post/comment IDs from saved_posts.csv or saved_comments.csv."""
    if not csv_path.exists():
        return set()
    try:
        with csv_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            return {row["id"] for row in reader if row.get("id")}
    except (OSError, UnicodeDecodeError, KeyError) as e:
        logger.warning(f"Failed to load saved IDs from {csv_path}: {e}")
        return set()
