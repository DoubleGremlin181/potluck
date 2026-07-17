"""Golden test (#144): the committed YNAB fixture yields exact results.

The fixture is generator output only (see tests/fixtures/README.md); the
regeneration one-liner lives in potluck/testing/ynab.py. 60 register rows
(6 of them raw duplicates that import via #N suffixes) plus the Plan decoy
member the parser must never read.

The milliunit-sum assertions are the acceptance criterion made executable:
the DB total must equal the generator's closed-form integer arithmetic to the
last milliunit — any float anywhere in the pipeline breaks them instantly.
"""

import logging
from pathlib import Path

import pytest

from potluck.services.context import AppContext
from potluck.services.imports import import_path
from potluck.services.items import get_item
from potluck.testing.ynab import expected_duplicate_suffix_count, expected_milliunit_sum

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "ynab" / "ynab-synth-001"

GOLDEN_ROWS = 60
GOLDEN_SEED = 13  # fixture regeneration argument, pinned for the record

# Closed-form results for rows=60, pinned as literals so BOTH generator drift
# and parser drift are caught (the closed forms are asserted against these
# same numbers below).
GOLDEN_MILLIUNIT_SUM = 3_041_000
GOLDEN_DUPLICATE_SUFFIXES = 6

# Identity stability anchor: sha256 of the budget name + first raw row. This
# can only change if the identity policy itself changes — bump parser_version
# and say so in the commit if it does.
GOLDEN_FIRST_EID = "ynab:019c5cb00053cebb227d5b51321a8a121ae947a6046536a2b9937b6ac143f94d"


def test_fixture_exists() -> None:
    for member in (
        "Synthetic Budget as of 2026-01-01 20-15 - Register.csv",
        "Synthetic Budget as of 2026-01-01 20-15 - Plan.csv",
    ):
        assert (FIXTURE / member).is_file(), member


def test_closed_forms_match_pinned_literals() -> None:
    assert expected_milliunit_sum(GOLDEN_ROWS) == GOLDEN_MILLIUNIT_SUM
    assert expected_duplicate_suffix_count(GOLDEN_ROWS) == GOLDEN_DUPLICATE_SUFFIXES


def test_golden_import_counts_and_exact_sum(
    ctx: AppContext, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        [run] = import_path(ctx, FIXTURE)
    assert run.source == "ynab"
    assert run.status == "completed"
    assert run.items_new == GOLDEN_ROWS
    assert run.items_duplicate == 0
    assert not [r for r in caplog.records if r.name.startswith("potluck")]

    with ctx.db.read() as conn:
        kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM items GROUP BY kind").fetchall())
        total = conn.execute("SELECT SUM(amount_milliunits) FROM transactions").fetchone()[0]
        satellite_rows = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert kinds == {"transaction": GOLDEN_ROWS}
    assert satellite_rows == GOLDEN_ROWS
    assert total == GOLDEN_MILLIUNIT_SUM  # exact to the last milliunit


def test_golden_identities_stable(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        eids = {str(r[0]) for r in conn.execute("SELECT external_id FROM items").fetchall()}
    assert GOLDEN_FIRST_EID in eids
    assert all(eid.startswith("ynab:") for eid in eids)
    suffixed = {eid for eid in eids if "#" in eid}
    assert len(suffixed) == GOLDEN_DUPLICATE_SUFFIXES
    assert all(eid.endswith("#2") for eid in suffixed)  # each dup group has 2


def test_golden_shapes(ctx: AppContext) -> None:
    """Structural facts the fixture pins: transfers keep verbatim payees and
    stay uncategorized, every row carries a date and an account, cleared
    status rides meta."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        transfers = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE payee LIKE 'Transfer : %'"
        ).fetchone()[0]
        categorized_transfers = conn.execute(
            "SELECT COUNT(*) FROM transactions "
            "WHERE payee LIKE 'Transfer : %' AND category IS NOT NULL"
        ).fetchone()[0]
        undated = conn.execute("SELECT COUNT(*) FROM items WHERE ts IS NULL").fetchone()[0]
        without_account = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE account IS NULL"
        ).fetchone()[0]
        without_cleared = conn.execute(
            "SELECT COUNT(*) FROM items WHERE json_extract(meta, '$.cleared') IS NULL"
        ).fetchone()[0]
        without_budget = conn.execute(
            "SELECT COUNT(*) FROM items WHERE json_extract(meta, '$.budget') "
            "IS DISTINCT FROM 'Synthetic Budget'"
        ).fetchone()[0]
    assert transfers > 0
    assert categorized_transfers == 0
    assert undated == 0
    assert without_account == 0
    assert without_cleared == 0
    assert without_budget == 0


def test_golden_item_detail_hydrates_satellite(ctx: AppContext) -> None:
    """Row 0 of the fixture, end to end through the satellite reader: the
    everyday-outflow closed form is -(4990 + 0) milliunits, uncategorized
    (0 % 6 == 0), on the first rotating account."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        item_id = int(
            conn.execute(
                "SELECT id FROM items WHERE external_id = ?", (GOLDEN_FIRST_EID,)
            ).fetchone()[0]
        )
    item = get_item(ctx, item_id)
    assert item.transaction is not None
    assert item.transaction.amount_milliunits == -4990
    assert item.transaction.account == "Synth Checking"
    assert item.transaction.category is None
    assert item.transaction.payee == item.title


def test_golden_reimport_is_noop(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    [run2] = import_path(ctx, FIXTURE)
    assert run2.items_new == 0
    assert run2.items_duplicate == GOLDEN_ROWS
    assert run2.items_updated == 0
