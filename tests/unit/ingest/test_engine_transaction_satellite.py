"""Engine satellite dispatch for transactions (#144): transactions rows ride
the same batch transaction, and item detail hydrates them."""

from datetime import UTC, datetime, timedelta

from potluck.models.drafts import TransactionDraft
from potluck.services.context import AppContext
from potluck.services.items import get_item
from tests.conftest import ingest_email_drafts


def _txn(n: int, *, amount: int = -4990, payee: str = "Corner Bakery") -> TransactionDraft:
    return TransactionDraft(
        external_id=f"ynab:fp{n}",
        ts=datetime(2025, 12, 31, tzinfo=UTC) - timedelta(days=n),
        title=payee,
        text="Fun Money: Dining Out",
        amount_milliunits=amount,
        account="Synth Checking",
        payee=payee,
        category="Dining Out",
        category_group="Fun Money",
    )


def _run(ctx: AppContext, *drafts: TransactionDraft) -> int:
    return ingest_email_drafts(ctx, *drafts, source_name="ynab", path="/tmp/export.zip")


def test_import_writes_transactions_satellite(ctx: AppContext) -> None:
    _run(ctx, _txn(1), _txn(2, amount=2000000, payee="Synth Employer"))
    with ctx.db.read() as conn:
        rows = conn.execute(
            """SELECT t.amount_milliunits, t.account, t.payee, t.category, t.category_group
               FROM transactions t JOIN items i ON i.id = t.item_id
               ORDER BY t.amount_milliunits"""
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["amount_milliunits"] == -4990
    assert rows[0]["account"] == "Synth Checking"
    assert rows[0]["payee"] == "Corner Bakery"
    assert rows[0]["category"] == "Dining Out"
    assert rows[0]["category_group"] == "Fun Money"
    assert rows[1]["amount_milliunits"] == 2000000


def test_exact_reimport_is_duplicate(ctx: AppContext) -> None:
    _run(ctx, _txn(1))
    import_id2 = _run(ctx, _txn(1))
    with ctx.db.read() as conn:
        imp = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id2,)).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert int(imp["items_duplicate"]) == 1
    assert count == 1


def test_amount_change_reingests_as_update(ctx: AppContext) -> None:
    """Satellite fields live inside the content hash (extra_hash_parts), so an
    amount-only change must be an UPDATE that rewrites the satellite row —
    never a duplicate that silently keeps the stale amount."""
    _run(ctx, _txn(1, amount=-4990))
    import_id2 = _run(ctx, _txn(1, amount=-5240))

    with ctx.db.read() as conn:
        imp = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id2,)).fetchone()
        amount = conn.execute("SELECT amount_milliunits FROM transactions").fetchone()[0]
        item_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert item_count == 1
    assert int(imp["items_updated"]) == 1
    assert amount == -5240


def test_get_item_hydrates_transaction_detail(ctx: AppContext) -> None:
    _run(ctx, _txn(1))
    with ctx.db.read() as conn:
        item_id = int(conn.execute("SELECT id FROM items").fetchone()[0])

    item = get_item(ctx, item_id)
    assert item.email is None
    assert item.message is None
    assert item.transaction is not None
    assert item.transaction.amount_milliunits == -4990
    assert item.transaction.account == "Synth Checking"
    assert item.transaction.payee == "Corner Bakery"
    assert item.transaction.category == "Dining Out"
    assert item.transaction.category_group == "Fun Money"
