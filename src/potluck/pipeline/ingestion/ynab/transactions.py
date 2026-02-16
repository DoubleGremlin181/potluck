"""YNAB Register CSV (transactions) ingestion."""

from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.financial import Account, AccountType, Transaction, TransactionType
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.utils.hashing import compute_content_hash
from potluck.pipeline.utils.parsers import parse_datetime

logger = get_logger(__name__)


def _parse_currency(value: str | None) -> Decimal:
    """Parse YNAB currency format ($1,234.56) to Decimal."""
    if not value:
        return Decimal("0.00")
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    if not cleaned:
        return Decimal("0.00")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        logger.warning(f"Could not parse currency value: {value!r}")
        return Decimal("0.00")


def ingest_transactions(
    register_path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[Account | Transaction]:
    """Ingest transactions and accounts from YNAB Register CSV.

    Two-pass approach:
    1. First pass: Collect unique account names and yield Account entities.
    2. Second pass: Yield Transaction entities linked to accounts.

    Args:
        register_path: Path to the Register CSV file.
        filters: Optional date range filters.

    Yields:
        Account entities first, then Transaction entities.
    """
    logger.info(f"Processing YNAB Register at {register_path}")

    rows = list(_read_register_csv(register_path))
    if not rows:
        return

    # Pass 1: Collect unique accounts and yield Account entities
    account_map: dict[str, UUID] = {}
    seen_accounts: set[str] = set()

    for row in rows:
        account_name = row.get("Account", "")
        if account_name and account_name not in seen_accounts:
            seen_accounts.add(account_name)
            account = Account(
                source_type=SourceType.YNAB,
                source_id=f"ynab_account:{account_name}",
                content_hash=compute_content_hash(f"ynab_account:{account_name}"),
                name=account_name,
                account_type=AccountType.CHECKING,
            )
            account_map[account_name] = account.id
            yield account

    # Pass 2: Yield Transaction entities
    for row in rows:
        account_name = row.get("Account", "")
        account_id = account_map.get(account_name)
        if not account_id:
            continue

        date_str = row.get("Date", "")
        occurred_at = parse_datetime(date_str)

        # Apply date filters
        if filters and occurred_at:
            if filters.since and occurred_at < filters.since:
                continue
            if filters.until and occurred_at >= filters.until:
                continue

        payee = row.get("Payee", "")
        memo = row.get("Memo")
        category = row.get("Category")
        category_group = row.get("Category Group")
        cleared = row.get("Cleared", "")

        inflow = _parse_currency(row.get("Inflow"))
        outflow = _parse_currency(row.get("Outflow"))
        amount = inflow - outflow

        # Determine transaction type
        is_transfer = bool(payee and payee.startswith("Transfer :"))
        if is_transfer:
            transaction_type = TransactionType.TRANSFER
        elif amount > 0:
            transaction_type = TransactionType.INCOME
        else:
            transaction_type = TransactionType.EXPENSE

        content_hash = compute_content_hash(f"ynab:{account_name}:{date_str}:{payee}:{amount}")

        yield Transaction(
            source_type=SourceType.YNAB,
            source_id=f"ynab_txn:{account_name}:{date_str}:{payee}",
            content_hash=content_hash,
            occurred_at=occurred_at,
            account_id=account_id,
            transaction_type=transaction_type,
            amount=amount,
            payee=payee if payee else None,
            description=memo if memo else None,
            category=category if category else None,
            category_group=category_group if category_group else None,
            is_cleared=cleared in ("Cleared", "Reconciled"),
            is_reconciled=cleared == "Reconciled",
            is_transfer=is_transfer,
        )


def _read_register_csv(register_path: Path) -> Iterator[dict[str, str]]:
    """Read Register CSV handling BOM and returning raw string dicts.

    We read the file manually instead of using parse_csv because YNAB's
    currency format ($1,234.56) confuses Polars' type inference.
    """
    import csv

    try:
        with register_path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            yield from reader
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Failed to read Register CSV {register_path}: {e}")
