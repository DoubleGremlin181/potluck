"""Financial models for transactions and accounts."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import ClassVar
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import Field, Relationship

from potluck.core.constants import MULTIMODAL_EMBEDDING_DIM, TEXT_EMBEDDING_DIM
from potluck.models.base import BaseEntity, TimestampedEntity


class AccountType(str, Enum):
    """Type of financial account."""

    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"
    CASH = "cash"
    INVESTMENT = "investment"
    LOAN = "loan"
    MORTGAGE = "mortgage"
    BALANCE = "balance"  # P2P apps like Venmo, PayPal, etc.
    OTHER = "other"


class TransactionType(str, Enum):
    """Type of financial transaction."""

    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"


class Account(BaseEntity, table=True):
    """Financial account (bank account, credit card, etc.).

    Stores account information from YNAB, bank exports, etc.
    """

    __tablename__ = "accounts"

    # Account details
    name: str = Field(
        description="Account name",
    )
    account_type: AccountType = Field(
        default=AccountType.CHECKING,
        description="Type of account",
    )
    institution: str | None = Field(
        default=None,
        description="Financial institution name",
    )

    # Balance information
    current_balance: Decimal | None = Field(
        default=None,
        decimal_places=2,
        description="Current balance",
    )
    currency: str = Field(
        default="USD",
        description="Currency code (ISO 4217)",
    )

    # Account metadata
    account_number_last4: str | None = Field(
        default=None,
        description="Last 4 digits of account number",
    )
    is_closed: bool = Field(
        default=False,
        description="Whether the account is closed",
    )
    closed_at: datetime | None = Field(
        default=None,
        description="When the account was closed",
    )

    # For budget tracking
    is_on_budget: bool = Field(
        default=True,
        description="Whether included in budget (YNAB concept)",
    )
    is_tracking: bool = Field(
        default=False,
        description="Whether this is a tracking account",
    )

    # Notes
    notes: str | None = Field(
        default=None,
        description="User notes about the account",
    )

    # Relationships
    transactions: list["Transaction"] = Relationship(
        back_populates="account",
        sa_relationship_kwargs={"foreign_keys": "[Transaction.account_id]"},
    )


class Transaction(TimestampedEntity, table=True):
    """Financial transaction.

    Stores individual transactions from bank exports, YNAB, etc.
    """

    __tablename__ = "transactions"

    # Search configuration - payee is priority, description/category auto-discovered
    __searchable__: ClassVar[bool] = True
    __search_exclude_fields__: ClassVar[set[str]] = set()
    __search_priority_fields__: ClassVar[set[str]] = {"payee"}
    __search_date_fields__: ClassVar[set[str]] = {"occurred_at"}

    def to_text_repr(self) -> str:
        """Return text representation with ID for LLM context."""
        payee = self.payee or "Unknown"
        amount_str = f"${self.amount:,.2f}" if self.amount else ""
        category = self.category or "Uncategorized"
        date_str = f" | date: {self.occurred_at.date()}" if self.occurred_at else ""
        return f"Transaction (id: {self.id}): {payee} {amount_str} ({category}){date_str}"

    # Account relationship
    account_id: UUID = Field(
        foreign_key="accounts.id",
        index=True,
        description="Account this transaction belongs to",
    )

    # Transaction details
    transaction_type: TransactionType = Field(
        default=TransactionType.EXPENSE,
        description="Type of transaction",
    )
    amount: Decimal = Field(
        decimal_places=2,
        description="Transaction amount (positive for income, negative for expense)",
    )
    currency: str = Field(
        default="USD",
        description="Currency code",
    )

    # Payee/description
    payee: str | None = Field(
        default=None,
        index=True,
        description="Who the payment was to/from",
    )
    payee_id: UUID | None = Field(
        default=None,
        foreign_key="people.id",
        description="Matched Person record for payee",
    )
    description: str | None = Field(
        default=None,
        description="Transaction description/memo",
    )
    original_description: str | None = Field(
        default=None,
        description="Original bank description before cleanup",
    )

    # Categorization (YNAB-style: category within a category_group)
    category: str | None = Field(
        default=None,
        index=True,
        description="Budget category name (e.g., 'Groceries', 'Rent')",
    )
    category_group: str | None = Field(
        default=None,
        description="Parent category group (e.g., 'Food', 'Housing')",
    )

    # Status
    is_cleared: bool = Field(
        default=False,
        description="Whether the transaction has cleared",
    )
    is_reconciled: bool = Field(
        default=False,
        description="Whether reconciled with bank statement",
    )
    is_pending: bool = Field(
        default=False,
        description="Whether the transaction is pending",
    )

    # Transfer tracking (use is_transfer flag, payee fields for transfer destination)
    is_transfer: bool = Field(
        default=False,
        description="Whether this is a transfer between accounts",
    )
    transfer_account_id: UUID | None = Field(
        default=None,
        foreign_key="accounts.id",
        description="Destination account if this is a transfer",
    )

    # Location (if from receipt/GPS)
    merchant_location: str | None = Field(
        default=None,
        description="Merchant location if known",
    )
    latitude: float | None = Field(
        default=None,
        description="Transaction location latitude",
    )
    longitude: float | None = Field(
        default=None,
        description="Transaction location longitude",
    )

    # Embeddings for semantic search
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(TEXT_EMBEDDING_DIM)),
        description="Text embedding for text-to-text semantic search",
    )
    multimodal_embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(MULTIMODAL_EMBEDDING_DIM)),
        description="Multimodal embedding for text-to-image cross-modal search",
    )

    # Full-text search vector (auto-populated by database trigger)
    search_vector: str | None = Field(
        default=None,
        sa_column=Column(TSVECTOR),
        description="FTS vector for keyword search (auto-populated by trigger)",
    )

    # Relationships
    account: Account = Relationship(
        back_populates="transactions",
        sa_relationship_kwargs={"foreign_keys": "[Transaction.account_id]"},
    )


class Budget(BaseEntity, table=True):
    """Budget allocation for a category/month (YNAB-style budgeting)."""

    __tablename__ = "budgets"

    # Budget period
    year: int = Field(
        ge=1,
        index=True,
        description="Budget year",
    )
    month: int = Field(
        ge=1,
        le=12,
        description="Budget month (1-12)",
    )

    # Category
    category: str = Field(
        index=True,
        description="Budget category name",
    )
    category_group: str | None = Field(
        default=None,
        description="Parent category group",
    )

    # Amounts
    budgeted: Decimal = Field(
        decimal_places=2,
        description="Amount budgeted for the month",
    )
    spent: Decimal = Field(
        default=Decimal("0.00"),
        decimal_places=2,
        description="Amount spent in the category",
    )
    available: Decimal | None = Field(
        default=None,
        decimal_places=2,
        description="Amount remaining (budgeted - spent + carryover)",
    )

    # Carryover from previous month
    carryover: Decimal = Field(
        default=Decimal("0.00"),
        decimal_places=2,
        description="Amount carried over from previous month",
    )

    currency: str = Field(
        default="USD",
        description="Currency code",
    )
    # Note: Use tags field for any notes/annotations on the budget
