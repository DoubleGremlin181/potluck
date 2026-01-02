"""Tests for Account, Transaction, and Budget models."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from potluck.models.financial import (
    Account,
    AccountType,
    Budget,
    Transaction,
    TransactionType,
)


class TestFinancialModels:
    """Tests for Account, Transaction, and Budget models."""

    def test_account_creation(self) -> None:
        """Account can be created."""
        account = Account(
            source_type="ynab",
            name="Checking Account",
        )
        assert account.name == "Checking Account"
        assert account.account_type == AccountType.CHECKING
        assert account.currency == "USD"
        assert account.is_closed is False

    def test_account_type_enum(self) -> None:
        """AccountType enum has expected values."""
        expected = {
            "checking",
            "savings",
            "credit_card",
            "cash",
            "investment",
            "loan",
            "mortgage",
            "balance",  # P2P apps like Venmo
            "other",
        }
        actual = {t.value for t in AccountType}
        assert actual == expected

    def test_transaction_creation(self) -> None:
        """Transaction can be created."""
        transaction = Transaction(
            source_type="ynab",
            account_id=uuid4(),
            occurred_at=datetime.now(UTC),
            amount=Decimal("-50.00"),
        )
        assert transaction.amount == Decimal("-50.00")
        assert transaction.transaction_type == TransactionType.EXPENSE
        assert transaction.is_cleared is False

    def test_transaction_type_enum(self) -> None:
        """TransactionType enum has expected values."""
        expected = {"income", "expense", "transfer", "refund", "adjustment"}
        actual = {t.value for t in TransactionType}
        assert actual == expected

    def test_budget_creation(self) -> None:
        """Budget can be created."""
        budget = Budget(
            source_type="ynab",
            year=2024,
            month=6,
            category="Groceries",
            budgeted=Decimal("500.00"),
        )
        assert budget.year == 2024
        assert budget.month == 6
        assert budget.budgeted == Decimal("500.00")

    def test_budget_month_validation(self) -> None:
        """Budget month must be between 1 and 12."""
        # Valid months work
        Budget(source_type="ynab", year=2024, month=1, category="Test", budgeted=Decimal("100"))
        Budget(source_type="ynab", year=2024, month=12, category="Test", budgeted=Decimal("100"))

        # Invalid months raise error
        with pytest.raises(ValidationError):
            Budget.model_validate(
                {
                    "source_type": "ynab",
                    "year": 2024,
                    "month": 13,
                    "category": "Test",
                    "budgeted": Decimal("100"),
                }
            )
        with pytest.raises(ValidationError):
            Budget.model_validate(
                {
                    "source_type": "ynab",
                    "year": 2024,
                    "month": 0,
                    "category": "Test",
                    "budgeted": Decimal("100"),
                }
            )
