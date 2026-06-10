"""Tests for potluck.ingest.engine: run_import batch ingest + dedup."""

import math
import sqlite3
from collections.abc import Iterator, Sequence
from datetime import datetime

import pytest

import potluck.ingest.engine as engine_mod
from potluck.ingest.engine import run_import
from potluck.models.drafts import NoteDraft
from potluck.services.context import AppContext
from potluck.storage import fts
from potluck.storage import items as items_storage
from potluck.storage.items import ItemRow
from potluck.testing.generators import synthetic_notes


def _make_drafts(count: int, seed: int = 42) -> list[NoteDraft]:
    return [
        NoteDraft(
            title=n["title"],
            text=n["text"],
            ts=datetime.fromisoformat(n["ts"]),
        )
        for n in synthetic_notes(count, seed)
    ]


def _run(
    ctx: AppContext,
    drafts: list[NoteDraft],
    *,
    source_name: str = "test-src",
    batch_size: int = 1000,
    on_progress: list[int] | None = None,
) -> int:
    return run_import(
        ctx.db,
        source_name=source_name,
        parser_version=1,
        drafts=iter(drafts),
        path="/tmp/test.zip",
        file_hash=None,
        batch_size=batch_size,
        on_progress=on_progress.append if on_progress is not None else None,
    )


def test_first_import_inserts_all(ctx: AppContext) -> None:
    drafts = _make_drafts(2500)
    import_id = _run(ctx, drafts)

    with ctx.db.read() as conn:
        items_count = int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
        imp_row = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id,)).fetchone()
        # Verify FTS triggers fired: "amber" appears in synthetic notes
        fts_rows = conn.execute(
            "SELECT rowid FROM items_fts WHERE items_fts MATCH ?", ("amber",)
        ).fetchall()

    assert items_count == 2500
    assert imp_row["status"] == "completed"
    assert int(imp_row["items_new"]) == 2500
    assert int(imp_row["items_duplicate"]) == 0
    assert imp_row["started_at"] is not None
    assert imp_row["finished_at"] is not None
    assert len(fts_rows) > 0, "FTS MATCH 'amber' should return results (triggers fired)"


def test_reimport_is_noop(ctx: AppContext) -> None:
    drafts = _make_drafts(2500)
    _run(ctx, drafts)
    import_id2 = _run(ctx, drafts)

    with ctx.db.read() as conn:
        items_count = int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
        imp_row = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id2,)).fetchone()

    assert items_count == 2500
    assert int(imp_row["items_new"]) == 0
    assert int(imp_row["items_duplicate"]) == 2500
    assert imp_row["status"] == "completed"


def test_in_batch_duplicates_collapse(ctx: AppContext) -> None:
    draft = NoteDraft(title="dup", text="same")
    import_id = _run(ctx, [draft, draft, draft], batch_size=1000)

    with ctx.db.read() as conn:
        items_count = int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
        imp_row = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id,)).fetchone()

    assert items_count == 1
    assert int(imp_row["items_new"]) == 1
    assert int(imp_row["items_duplicate"]) == 2


def test_cross_batch_duplicates_collapse(ctx: AppContext) -> None:
    a = NoteDraft(title="A", text="unique-a")
    b = NoteDraft(title="B", text="unique-b")
    # batch_size=2 → batch1=[A,B], batch2=[A]; A crosses batch boundary
    import_id = _run(ctx, [a, b, a], batch_size=2)

    with ctx.db.read() as conn:
        items_count = int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
        imp_row = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id,)).fetchone()

    assert items_count == 2
    assert int(imp_row["items_new"]) == 2
    assert int(imp_row["items_duplicate"]) == 1


def test_crash_mid_import_consistent(ctx: AppContext) -> None:
    full_drafts = _make_drafts(3000)

    def _crashing_iter() -> Iterator[NoteDraft]:
        for i, d in enumerate(full_drafts):
            if i == 2000:
                raise RuntimeError("disk full")
            yield d

    with pytest.raises(RuntimeError, match="disk full"):
        run_import(
            ctx.db,
            source_name="crashing-src",
            parser_version=1,
            drafts=_crashing_iter(),
            path="/tmp/crash.zip",
            file_hash=None,
            batch_size=1000,
        )

    with ctx.db.read() as conn:
        imp_row = conn.execute("SELECT * FROM imports WHERE path = '/tmp/crash.zip'").fetchone()
        committed_count = int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])

    assert imp_row["status"] == "failed"
    assert "disk full" in str(imp_row["error"])
    # First 2 batches (1000 each) should be committed; third batch never started
    assert committed_count == 2000

    # FTS index must be consistent after partial import (integrity_check needs a write conn)
    ctx.db.write(fts.integrity_check)

    # Re-run full set — only the missing 1000 items should be added
    import_id2 = run_import(
        ctx.db,
        source_name="crashing-src",
        parser_version=1,
        drafts=iter(full_drafts),
        path="/tmp/full.zip",
        file_hash=None,
        batch_size=1000,
    )
    with ctx.db.read() as conn:
        items_count = int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
        imp_row2 = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id2,)).fetchone()

    assert items_count == 3000
    assert int(imp_row2["items_new"]) == 1000
    assert int(imp_row2["items_duplicate"]) == 2000


def test_original_error_not_masked_when_ledger_update_fails(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If finish_import raises while recording failure, run_import re-raises the
    original generator exception, not the ledger error."""

    def _boom_iter() -> Iterator[NoteDraft]:
        raise ValueError("boom")
        yield  # make it a generator

    def _bad_finish_import(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("ledger gone")

    monkeypatch.setattr(engine_mod, "finish_import", _bad_finish_import)

    with pytest.raises(ValueError, match="boom"):
        run_import(
            ctx.db,
            source_name="mask-test-src",
            parser_version=1,
            drafts=_boom_iter(),
            path="/tmp/mask.zip",
            file_hash=None,
        )


def test_one_dedup_query_and_one_insert_per_batch(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    n = 2500
    batch_size = 1000
    expected_batches = math.ceil(n / batch_size)  # 3

    call_counts = {"existing": 0, "insert": 0}
    real_existing = items_storage.existing_hashes
    real_insert = items_storage.insert_items

    def counting_existing(conn: sqlite3.Connection, hashes: Sequence[str]) -> set[str]:
        call_counts["existing"] += 1
        return real_existing(conn, hashes)

    def counting_insert(conn: sqlite3.Connection, rows: Sequence[ItemRow]) -> None:
        call_counts["insert"] += 1
        real_insert(conn, rows)

    monkeypatch.setattr(engine_mod, "existing_hashes", counting_existing)
    monkeypatch.setattr(engine_mod, "insert_items", counting_insert)

    drafts = _make_drafts(n)
    _run(ctx, drafts, batch_size=batch_size)

    assert call_counts["existing"] == expected_batches, (
        f"expected {expected_batches} existing_hashes calls, got {call_counts['existing']}"
    )
    assert call_counts["insert"] == expected_batches, (
        f"expected {expected_batches} insert_items calls, got {call_counts['insert']}"
    )


def test_on_progress_called(ctx: AppContext) -> None:
    progress: list[int] = []
    drafts = _make_drafts(2500)
    _run(ctx, drafts, batch_size=1000, on_progress=progress)

    # Called after each batch: 1000, 2000, 2500
    assert progress == [1000, 2000, 2500]
