"""YNAB (You Need A Budget) export ingestion stage.

Handles importing data from YNAB budget exports including:
- Register CSV — Transactions and accounts
- Plan CSV — Monthly budget allocations
"""

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from potluck.core.logging import get_logger
from potluck.models.base import EntityType, IngestableEntity, SourceType
from potluck.pipeline.dtos import DetectionResult, PipelineFilter
from potluck.pipeline.ingestion.base import BaseIngestionStage
from potluck.pipeline.ingestion.registry import register
from potluck.pipeline.ingestion.ynab.budget import ingest_budgets
from potluck.pipeline.ingestion.ynab.transactions import ingest_transactions
from potluck.pipeline.utils.archive import extracted

logger = get_logger(__name__)


@register
class YNABStage(BaseIngestionStage):
    """Ingestion stage for YNAB budget export archives.

    Supports both ZIP archives and extracted directories containing
    *Register.csv and *Plan.csv files.
    """

    SOURCE_TYPE: ClassVar[SourceType] = SourceType.YNAB

    FILENAME_PATTERNS: ClassVar[list[str]] = [
        r"YNAB Export.*\.zip",  # "YNAB Export - My Budget as of 2026-01-01 20-15.zip"
        r".*Budget.*as of.*\.zip",  # Alternative naming
        r"My Budget as of.*",  # Extracted folder
    ]

    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {
        EntityType.TRANSACTION,
        EntityType.BUDGET,
    }

    def detect(self, path: Path) -> DetectionResult:
        """Scan the YNAB export and return available entity types with counts."""
        with extracted(path) as content_path:
            return self._detect_from_path(content_path)

    def _detect_from_path(self, path: Path) -> DetectionResult:
        entity_counts: dict[EntityType, int] = {}
        metadata: dict[str, str] = {}

        register_path = _find_csv(path, "Register")
        if register_path:
            count = _count_csv_rows(register_path)
            if count > 0:
                entity_counts[EntityType.TRANSACTION] = count

        plan_path = _find_csv(path, "Plan")
        if plan_path:
            count = _count_csv_rows(plan_path)
            if count > 0:
                entity_counts[EntityType.BUDGET] = count

        if entity_counts:
            metadata["source"] = "YNAB Export"

        return DetectionResult(entity_counts=entity_counts, metadata=metadata)

    def execute(
        self,
        path: Path,
        entity_types: set[EntityType] | None = None,
        filters: PipelineFilter | None = None,
    ) -> Iterator[IngestableEntity]:
        """Yield entities from the YNAB export."""
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

        logger.info(f"Processing YNAB export at {path} for types: {types_to_process}")

        if EntityType.TRANSACTION in types_to_process:
            register_path = _find_csv(path, "Register")
            if register_path:
                yield from ingest_transactions(register_path, filters)

        if EntityType.BUDGET in types_to_process:
            plan_path = _find_csv(path, "Plan")
            if plan_path:
                yield from ingest_budgets(plan_path)


def _find_csv(path: Path, suffix: str) -> Path | None:
    """Find a YNAB CSV file by suffix pattern (*Register.csv or *Plan.csv)."""
    # Search in the given path and one level of subdirectories
    for search_path in [path, *path.iterdir()] if path.is_dir() else [path]:
        if not search_path.is_dir():
            continue
        for csv_file in search_path.glob(f"*{suffix}.csv"):
            return csv_file
    return None


def _count_csv_rows(csv_path: Path) -> int:
    """Count data rows in a CSV file (excluding header), handling BOM."""
    import csv

    try:
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # Skip header
            return sum(1 for _ in reader)
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Failed to count rows in {csv_path}: {e}")
        return 0
