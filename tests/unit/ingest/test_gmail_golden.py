"""Golden test (#125): the committed Gmail Takeout fixture yields exact results.

The fixture is generator output only (see tests/fixtures/README.md); the
regeneration one-liner lives in potluck/testing/mbox.py.
"""

import json
from pathlib import Path

from potluck.services.context import AppContext
from potluck.services.imports import import_path

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "gmail" / "takeout-synth-001"

GOLDEN_COUNT = 25
GOLDEN_SEED = 7


def test_fixture_exists() -> None:
    mbox = FIXTURE / "Takeout" / "Mail" / "All mail Including Spam and Trash.mbox"
    assert mbox.is_file(), "committed Gmail fixture missing"


def test_golden_import_counts(ctx: AppContext) -> None:
    [run] = import_path(ctx, FIXTURE)
    assert run.source == "gmail"
    assert run.status == "completed"
    assert run.items_new == GOLDEN_COUNT
    assert run.items_duplicate == 0

    with ctx.db.read() as conn:
        items = conn.execute("SELECT COUNT(*) FROM items WHERE kind = 'email'").fetchone()[0]
        emails = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    assert items == GOLDEN_COUNT
    assert emails == GOLDEN_COUNT


def test_golden_threads_reconstructed(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        multi = conn.execute(
            """SELECT thread_key, COUNT(*) AS n FROM emails
               GROUP BY thread_key HAVING n > 1 ORDER BY n DESC"""
        ).fetchall()
        linked = conn.execute("SELECT COUNT(*) FROM items WHERE parent_id IS NOT NULL").fetchone()[
            0
        ]
    assert multi, "expected at least one multi-message conversation in 25 messages"
    assert linked > 0, "expected at least one reply linked to its parent"


def test_golden_labels_preserved(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        rows = conn.execute("SELECT labels_json FROM emails").fetchall()
    label_sets = [json.loads(str(r[0])) for r in rows]
    assert any("Inbox" in labels for labels in label_sets)


def test_golden_identities_stable(ctx: AppContext) -> None:
    """external_ids are pinned to the generator's deterministic Message-IDs."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        eids = {
            str(r[0])
            for r in conn.execute("SELECT external_id FROM items WHERE kind='email'").fetchall()
        }
    assert f"mid:synth-{GOLDEN_SEED}-000000@potluck.test" in eids
    assert all(eid.startswith(("mid:", "noid:")) for eid in eids)


def test_golden_reimport_is_noop(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    [run2] = import_path(ctx, FIXTURE)
    assert run2.items_new == 0
    assert run2.items_duplicate == GOLDEN_COUNT
    assert run2.items_updated == 0
