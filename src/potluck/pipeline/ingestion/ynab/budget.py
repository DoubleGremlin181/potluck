"""YNAB Plan CSV (budget) ingestion."""

import csv
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from potluck.core.exceptions import IngestionError
from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.financial import Budget
from potluck.pipeline.ingestion.ynab.transactions import _parse_currency

logger = get_logger(__name__)


def ingest_budgets(plan_path: Path) -> Iterator[Budget]:
    """Ingest budget allocations from YNAB Plan CSV.

    Args:
        plan_path: Path to the Plan CSV file.

    Yields:
        Budget entities.
    """
    logger.info(f"Processing YNAB Plan at {plan_path}")

    try:
        with plan_path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                month_str = row.get("Month", "")
                if not month_str:
                    continue

                try:
                    month_dt = datetime.strptime(month_str, "%b %Y")
                except ValueError:
                    logger.warning(f"Could not parse budget month: {month_str!r}")
                    continue

                category = row.get("Category", "")
                category_group = row.get("Category Group")
                if not category:
                    continue

                assigned = _parse_currency(row.get("Assigned"))
                activity = _parse_currency(row.get("Activity"))
                available = _parse_currency(row.get("Available"))
                if assigned is None or activity is None or available is None:
                    logger.warning(
                        f"Skipping budget entry with unparseable currency: {category} {month_str}"
                    )
                    continue

                # Activity is negative for spending in YNAB; spent should be positive
                spent = -activity

                year = month_dt.year
                month = month_dt.month

                yield Budget(
                    source_type=SourceType.YNAB,
                    year=year,
                    month=month,
                    category=category,
                    category_group=category_group if category_group else None,
                    budgeted=assigned,
                    spent=spent,
                    available=available,
                )
    except (OSError, UnicodeDecodeError) as e:
        raise IngestionError(f"Failed to read Plan CSV {plan_path}: {e}") from e
