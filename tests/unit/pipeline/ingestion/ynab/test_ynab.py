"""Tests for YNAB export ingester."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from potluck.core.exceptions import IngestionError
from potluck.models.base import EntityType, SourceType
from potluck.models.financial import Account, Budget, Transaction, TransactionType
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.ingestion.ynab import YNABStage
from potluck.pipeline.ingestion.ynab.transactions import _parse_currency
from potluck.pipeline.utils.hashing import compute_content_hash

FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "ynab"


class TestYNABCurrencyParsing:
    """Tests for YNAB currency format parsing."""

    def test_parse_simple_amount(self) -> None:
        """Simple dollar amounts are parsed correctly."""
        assert _parse_currency("$125.50") == Decimal("125.50")

    def test_parse_thousands(self) -> None:
        """Amounts with comma separators are parsed."""
        assert _parse_currency("$1,234.56") == Decimal("1234.56")

    def test_parse_zero(self) -> None:
        """Zero amounts are parsed."""
        assert _parse_currency("$0.00") == Decimal("0.00")

    def test_parse_none(self) -> None:
        """None returns zero."""
        assert _parse_currency(None) == Decimal("0.00")

    def test_parse_empty(self) -> None:
        """Empty string returns zero."""
        assert _parse_currency("") == Decimal("0.00")


class TestYNABDetection:
    """Tests for YNABStage.detect()."""

    def test_detect_finds_transactions_and_budgets(self) -> None:
        """Detection finds both Register and Plan CSVs."""
        stage = YNABStage()
        result = stage.detect(FIXTURES_DIR)

        assert EntityType.TRANSACTION in result.entity_counts
        assert EntityType.BUDGET in result.entity_counts
        assert result.entity_counts[EntityType.TRANSACTION] == 8
        assert result.entity_counts[EntityType.BUDGET] == 9

    def test_detect_empty_directory(self, tmp_path: Path) -> None:
        """Detection returns empty counts for directory without CSVs."""
        stage = YNABStage()
        result = stage.detect(tmp_path)
        assert result.entity_counts == {}


class TestYNABTransactionIngestion:
    """Tests for YNAB transaction and account ingestion."""

    def test_accounts_extracted(self) -> None:
        """Unique accounts are extracted and yielded first."""
        stage = YNABStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.TRANSACTION}))

        accounts = [e for e in entities if isinstance(e, Account)]
        account_names = {a.name for a in accounts}
        assert account_names == {"Checking", "Savings", "Credit Card"}
        assert all(a.source_type == SourceType.YNAB for a in accounts)

    def test_transactions_ingested(self) -> None:
        """Transactions are ingested with correct field mapping."""
        stage = YNABStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.TRANSACTION}))

        transactions = [e for e in entities if isinstance(e, Transaction)]
        assert len(transactions) == 8

    def test_transaction_amounts(self) -> None:
        """Amounts are calculated correctly (inflow - outflow)."""
        stage = YNABStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.TRANSACTION}))
        transactions = [e for e in entities if isinstance(e, Transaction)]

        # Grocery store: outflow $125.50
        grocery = next(t for t in transactions if t.payee == "Grocery Store")
        assert grocery.amount == Decimal("-125.50")

        # Employer: inflow $2,500.00
        salary = next(t for t in transactions if t.payee == "Employer Inc")
        assert salary.amount == Decimal("2500.00")

        # Large amount: $1,234.56
        large = next(t for t in transactions if t.payee == "Online Store")
        assert large.amount == Decimal("-1234.56")

    def test_transfer_detection(self) -> None:
        """Transfers are detected from payee pattern."""
        stage = YNABStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.TRANSACTION}))
        transactions = [e for e in entities if isinstance(e, Transaction)]

        transfers = [t for t in transactions if t.is_transfer]
        assert len(transfers) == 2
        assert all(t.transaction_type == TransactionType.TRANSFER for t in transfers)

    def test_transaction_type_classification(self) -> None:
        """Transaction types are correctly classified."""
        stage = YNABStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.TRANSACTION}))
        transactions = [e for e in entities if isinstance(e, Transaction)]

        salary = next(t for t in transactions if t.payee == "Employer Inc")
        assert salary.transaction_type == TransactionType.INCOME

        grocery = next(t for t in transactions if t.payee == "Grocery Store")
        assert grocery.transaction_type == TransactionType.EXPENSE

    def test_cleared_status(self) -> None:
        """Cleared and reconciled statuses are mapped correctly."""
        stage = YNABStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.TRANSACTION}))
        transactions = [e for e in entities if isinstance(e, Transaction)]

        cleared = next(t for t in transactions if t.payee == "Grocery Store")
        assert cleared.is_cleared is True
        assert cleared.is_reconciled is False

        reconciled = next(t for t in transactions if t.payee == "Electric Company")
        assert reconciled.is_cleared is True
        assert reconciled.is_reconciled is True

        uncleared = next(t for t in transactions if t.payee == "Coffee Shop")
        assert uncleared.is_cleared is False

    def test_category_mapping(self) -> None:
        """Categories and category groups are mapped."""
        stage = YNABStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.TRANSACTION}))
        transactions = [e for e in entities if isinstance(e, Transaction)]

        grocery = next(t for t in transactions if t.payee == "Grocery Store")
        assert grocery.category == "Groceries"
        assert grocery.category_group == "Food"

    def test_content_hash_deterministic(self) -> None:
        """Content hashes are deterministic."""
        stage = YNABStage()
        entities1 = list(stage.execute(FIXTURES_DIR, {EntityType.TRANSACTION}))
        entities2 = list(stage.execute(FIXTURES_DIR, {EntityType.TRANSACTION}))

        txns1 = sorted(
            [e for e in entities1 if isinstance(e, Transaction)],
            key=lambda t: t.content_hash or "",
        )
        txns2 = sorted(
            [e for e in entities2 if isinstance(e, Transaction)],
            key=lambda t: t.content_hash or "",
        )
        for t1, t2 in zip(txns1, txns2, strict=True):
            assert t1.content_hash == t2.content_hash


class TestYNABBudgetIngestion:
    """Tests for YNAB budget ingestion."""

    def test_budgets_ingested(self) -> None:
        """Budget entries are ingested correctly."""
        stage = YNABStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.BUDGET}))

        budgets = [e for e in entities if isinstance(e, Budget)]
        assert len(budgets) == 9

    def test_budget_month_parsing(self) -> None:
        """Month strings like 'Nov 2025' are parsed correctly."""
        stage = YNABStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.BUDGET}))
        budgets = [e for e in entities if isinstance(e, Budget)]

        nov_budgets = [b for b in budgets if b.year == 2025 and b.month == 11]
        assert len(nov_budgets) == 4

        dec_budgets = [b for b in budgets if b.year == 2025 and b.month == 12]
        assert len(dec_budgets) == 5

    def test_budget_amounts(self) -> None:
        """Budget amounts are parsed correctly with negative activity conversion."""
        stage = YNABStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.BUDGET}))
        budgets = [e for e in entities if isinstance(e, Budget)]

        nov_groceries = next(b for b in budgets if b.category == "Groceries" and b.month == 11)
        assert nov_groceries.budgeted == Decimal("600.00")
        assert nov_groceries.spent == Decimal("525.50")  # -(-525.50) = positive
        assert nov_groceries.available == Decimal("74.50")

    def test_budget_source_type(self) -> None:
        """Budgets have YNAB source type."""
        stage = YNABStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.BUDGET}))
        budgets = [e for e in entities if isinstance(e, Budget)]

        assert all(b.source_type == SourceType.YNAB for b in budgets)


class TestYNABEntityTypeFiltering:
    """Tests for entity type selection."""

    def test_only_transactions(self) -> None:
        """Only transactions/accounts returned when TRANSACTION requested."""
        stage = YNABStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.TRANSACTION}))
        assert all(isinstance(e, (Account, Transaction)) for e in entities)

    def test_only_budgets(self) -> None:
        """Only budgets returned when BUDGET requested."""
        stage = YNABStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.BUDGET}))
        assert all(isinstance(e, Budget) for e in entities)


class TestYNABDateFiltering:
    """Tests for date range filtering on transactions."""

    def test_since_filter(self) -> None:
        """Transactions before 'since' date are excluded."""
        stage = YNABStage()
        # Dec 18 — should exclude the 3 transactions from Dec 15-17
        # YNAB dates are parsed as naive datetimes (no timezone in CSV)
        since = datetime(2025, 12, 18)
        entities = list(
            stage.execute(FIXTURES_DIR, {EntityType.TRANSACTION}, PipelineFilter(since=since))
        )

        transactions = [e for e in entities if isinstance(e, Transaction)]
        assert all(t.occurred_at is not None and t.occurred_at >= since for t in transactions)
        assert len(transactions) == 5  # Dec 18 (x2), Dec 19, Dec 20 (x2)

    def test_until_filter(self) -> None:
        """Transactions after 'until' date are excluded."""
        stage = YNABStage()
        # Dec 18 — should exclude transactions from Dec 18 onward
        until = datetime(2025, 12, 18)
        entities = list(
            stage.execute(FIXTURES_DIR, {EntityType.TRANSACTION}, PipelineFilter(until=until))
        )

        transactions = [e for e in entities if isinstance(e, Transaction)]
        assert all(t.occurred_at is not None and t.occurred_at < until for t in transactions)
        assert len(transactions) == 3  # Dec 15, Dec 16, Dec 17


class TestYNABInvalidCurrency:
    """Tests for handling invalid currency values."""

    def test_invalid_currency_returns_none(self) -> None:
        """Unparseable currency values return None."""
        assert _parse_currency("not-a-number") is None
        assert _parse_currency("abc") is None


class TestYNABBudgetIngestionError:
    """Tests for IngestionError wrapping on budget CSV read failure."""

    def test_budget_csv_read_failure_raises_ingestion_error(self, tmp_path: Path) -> None:
        """Unreadable Plan CSV raises IngestionError."""
        from potluck.pipeline.ingestion.ynab.budget import ingest_budgets

        bad_csv = tmp_path / "Plan.csv"
        bad_csv.write_bytes(b"\x80\x81\x82")  # Invalid UTF-8

        with pytest.raises(IngestionError, match="Failed to read Plan CSV"):
            list(ingest_budgets(bad_csv))

    def test_budget_missing_file_raises_ingestion_error(self, tmp_path: Path) -> None:
        """Non-existent Plan CSV raises IngestionError."""
        from potluck.pipeline.ingestion.ynab.budget import ingest_budgets

        missing = tmp_path / "Plan.csv"

        with pytest.raises(IngestionError, match="Failed to read Plan CSV"):
            list(ingest_budgets(missing))


class TestYNABBudgetContentHashAndSourceId:
    """Tests for budget content_hash and source_id correctness."""

    def test_budget_source_id_format(self) -> None:
        """Budget source_id follows 'ynab_budget:YEAR:MONTH:CATEGORY' pattern."""
        stage = YNABStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.BUDGET}))
        budgets = [e for e in entities if isinstance(e, Budget)]

        for budget in budgets:
            assert budget.source_id is not None
            parts = budget.source_id.split(":")
            assert parts[0] == "ynab_budget"
            assert len(parts) == 4  # ynab_budget:year:month:category

    def test_budget_content_hash_uses_compute_content_hash(self) -> None:
        """Budget content_hash is computed from the source_id via compute_content_hash."""
        stage = YNABStage()
        entities = list(stage.execute(FIXTURES_DIR, {EntityType.BUDGET}))
        budgets = [e for e in entities if isinstance(e, Budget)]

        for budget in budgets:
            assert budget.source_id is not None
            expected_hash = compute_content_hash(budget.source_id)
            assert budget.content_hash == expected_hash

    def test_budget_content_hash_deterministic(self) -> None:
        """Budget content hashes are deterministic across runs."""
        stage = YNABStage()
        entities1 = list(stage.execute(FIXTURES_DIR, {EntityType.BUDGET}))
        entities2 = list(stage.execute(FIXTURES_DIR, {EntityType.BUDGET}))

        budgets1 = sorted(
            [e for e in entities1 if isinstance(e, Budget)],
            key=lambda b: b.source_id or "",
        )
        budgets2 = sorted(
            [e for e in entities2 if isinstance(e, Budget)],
            key=lambda b: b.source_id or "",
        )
        for b1, b2 in zip(budgets1, budgets2, strict=True):
            assert b1.content_hash == b2.content_hash
