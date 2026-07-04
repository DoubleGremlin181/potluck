"""Tests for potluck.ingest.engine: run_import batch ingest + dedup."""

import json
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
) -> int:
    return run_import(
        ctx.db,
        source_name=source_name,
        parser_version=1,
        drafts=iter(drafts),
        path="/tmp/test.zip",
        file_hash=None,
        batch_size=batch_size,
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
    assert int(imp_row["items_updated"]) == 0
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


def test_same_content_in_two_sources_stored_per_source(ctx: AppContext) -> None:
    """Content-hash dedup is scoped per source: identical content imported under
    two source names yields one row per source, not a cross-source duplicate."""
    _run(ctx, [NoteDraft(title="shared", text="identical body")], source_name="source-a")
    import_id2 = _run(
        ctx, [NoteDraft(title="shared", text="identical body")], source_name="source-b"
    )

    with ctx.db.read() as conn:
        count = int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
        sources = {
            str(r[0])
            for r in conn.execute(
                "SELECT s.name FROM items i JOIN sources s ON s.id = i.source_id"
            ).fetchall()
        }
        imp_row = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id2,)).fetchone()

    assert count == 2
    assert sources == {"source-a", "source-b"}
    assert int(imp_row["items_new"]) == 1
    assert int(imp_row["items_duplicate"]) == 0


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


def test_interrupt_marks_ledger_failed_and_reraises(ctx: AppContext) -> None:
    """Ctrl-C (KeyboardInterrupt is not an Exception) must not leave the
    ledger row 'running' forever: best-effort mark failed, then re-raise."""

    def _interrupted_iter() -> Iterator[NoteDraft]:
        yield NoteDraft(title="a", text="before interrupt")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_import(
            ctx.db,
            source_name="interrupt-src",
            parser_version=1,
            drafts=_interrupted_iter(),
            path="/tmp/interrupt.zip",
            file_hash=None,
            batch_size=1,
        )

    with ctx.db.read() as conn:
        imp_row = conn.execute("SELECT * FROM imports WHERE path = '/tmp/interrupt.zip'").fetchone()

    assert imp_row["status"] == "failed"
    assert "KeyboardInterrupt" in str(imp_row["error"])
    assert imp_row["finished_at"] is not None


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

    def counting_existing(
        conn: sqlite3.Connection, source_id: int, hashes: Sequence[str]
    ) -> set[str]:
        call_counts["existing"] += 1
        return real_existing(conn, source_id, hashes)

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


# ---------------------------------------------------------------------------
# Update-in-place: one logical item per (source, external_id)
# ---------------------------------------------------------------------------


def _eid_draft(external_id: str, *, title: str = "T", text: str = "body") -> NoteDraft:
    return NoteDraft(external_id=external_id, title=title, text=text)


def _import_counters(ctx: AppContext, import_id: int) -> tuple[int, int, int]:
    with ctx.db.read() as conn:
        row = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id,)).fetchone()
    return (
        int(row["items_new"]),
        int(row["items_duplicate"]),
        int(row["items_updated"]),
    )


def test_changed_content_updates_in_place(ctx: AppContext) -> None:
    _run(ctx, [_eid_draft("Keep/a.json", text="alphaversion")])
    with ctx.db.read() as conn:
        first = conn.execute("SELECT id, content_hash FROM items").fetchone()

    import_id2 = _run(ctx, [_eid_draft("Keep/a.json", text="betaversion")])

    with ctx.db.read() as conn:
        rows = conn.execute("SELECT id, text, content_hash, import_id FROM items").fetchall()
        new_hits = conn.execute(
            "SELECT rowid FROM items_fts WHERE items_fts MATCH ?", ("betaversion",)
        ).fetchall()
        old_hits = conn.execute(
            "SELECT rowid FROM items_fts WHERE items_fts MATCH ?", ("alphaversion",)
        ).fetchall()

    assert len(rows) == 1
    assert int(rows[0]["id"]) == int(first["id"])  # id is stable across updates
    assert rows[0]["text"] == "betaversion"
    assert rows[0]["content_hash"] != first["content_hash"]
    assert int(rows[0]["import_id"]) == import_id2
    assert _import_counters(ctx, import_id2) == (0, 0, 1)
    assert [int(r[0]) for r in new_hits] == [int(first["id"])]
    assert old_hits == []


def test_meta_only_change_refreshes_meta_without_fts_churn(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Meta is excluded from the content hash; a meta-only difference must still
    refresh the stored row — through the no-FTS-churn meta-only UPDATE shape."""
    _run(ctx, [_eid_draft("Keep/m.json", text="same").model_copy(update={"meta": {"a": 1}})])

    content_rows: list[int] = []
    meta_rows: list[int] = []
    real_content = items_storage.update_items_content
    real_meta = items_storage.update_items_meta

    def counting_content(conn: sqlite3.Connection, rows: Sequence[object]) -> None:
        content_rows.append(len(rows))
        real_content(conn, rows)  # type: ignore[arg-type]

    def counting_meta(conn: sqlite3.Connection, rows: Sequence[object]) -> None:
        meta_rows.append(len(rows))
        real_meta(conn, rows)  # type: ignore[arg-type]

    monkeypatch.setattr(engine_mod, "update_items_content", counting_content)
    monkeypatch.setattr(engine_mod, "update_items_meta", counting_meta)

    import_id2 = _run(
        ctx, [_eid_draft("Keep/m.json", text="same").model_copy(update={"meta": {"a": 2}})]
    )

    with ctx.db.read() as conn:
        row = conn.execute("SELECT meta, content_hash, import_id FROM items").fetchone()

    assert json.loads(row["meta"]) == {"a": 2}
    assert int(row["import_id"]) == import_id2
    assert _import_counters(ctx, import_id2) == (0, 0, 1)
    assert sum(content_rows) == 0, "meta-only change must not take the FTS-rewriting path"
    assert sum(meta_rows) == 1


def test_null_external_id_changed_content_inserts_new_row(ctx: AppContext) -> None:
    """Without an external_id there is no identity to update — hash dedup only."""
    _run(ctx, [NoteDraft(title="N", text="version-one")])
    import_id2 = _run(ctx, [NoteDraft(title="N", text="version-two")])

    with ctx.db.read() as conn:
        count = int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])

    assert count == 2
    assert _import_counters(ctx, import_id2) == (1, 0, 0)


def test_in_batch_same_external_id_last_wins(ctx: AppContext) -> None:
    drafts = [
        _eid_draft("Keep/x.json", text="first"),
        _eid_draft("Keep/x.json", text="second"),
    ]
    import_id = _run(ctx, drafts, batch_size=1000)

    with ctx.db.read() as conn:
        rows = conn.execute("SELECT text FROM items").fetchall()

    assert [r["text"] for r in rows] == ["second"]
    assert _import_counters(ctx, import_id) == (1, 1, 0)


def test_cross_batch_same_external_id_updates(ctx: AppContext) -> None:
    drafts = [
        _eid_draft("Keep/x.json", text="first"),
        _eid_draft("Keep/x.json", text="second"),
    ]
    import_id = _run(ctx, drafts, batch_size=1)

    with ctx.db.read() as conn:
        rows = conn.execute("SELECT text FROM items").fetchall()

    assert [r["text"] for r in rows] == ["second"]
    assert _import_counters(ctx, import_id) == (1, 0, 1)


def test_in_run_content_revert_last_wins(ctx: AppContext) -> None:
    """v1 → v2 → v1 for the same external_id across batches must end at v1
    (documented last-wins): the run-wide seen set is invalidated when an
    in-run update displaces a row's old content hash."""
    drafts = [
        _eid_draft("Keep/r.json", text="version-one"),
        _eid_draft("Keep/r.json", text="version-two"),
        _eid_draft("Keep/r.json", text="version-one"),
    ]
    import_id = _run(ctx, drafts, batch_size=1)

    with ctx.db.read() as conn:
        rows = conn.execute("SELECT text FROM items").fetchall()

    assert [r["text"] for r in rows] == ["version-one"]
    assert _import_counters(ctx, import_id) == (1, 0, 2)


def test_constant_queries_per_batch_with_external_ids(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The identity lookup and both UPDATE shapes run once per batch, like the
    hash dedup query — never per item."""
    n = 250
    batch_size = 100
    expected_batches = math.ceil(n / batch_size)  # 3

    calls = {"existing_eid": 0, "update_content": 0, "update_meta": 0}
    real_existing = items_storage.existing_by_external_id
    real_content = items_storage.update_items_content
    real_meta = items_storage.update_items_meta

    def counting_existing(
        conn: sqlite3.Connection, source_id: int, eids: Sequence[str]
    ) -> dict[str, items_storage.ExistingItem]:
        calls["existing_eid"] += 1
        return real_existing(conn, source_id, eids)

    def counting_content(conn: sqlite3.Connection, rows: Sequence[object]) -> None:
        calls["update_content"] += 1
        real_content(conn, rows)  # type: ignore[arg-type]

    def counting_meta(conn: sqlite3.Connection, rows: Sequence[object]) -> None:
        calls["update_meta"] += 1
        real_meta(conn, rows)  # type: ignore[arg-type]

    monkeypatch.setattr(engine_mod, "existing_by_external_id", counting_existing)
    monkeypatch.setattr(engine_mod, "update_items_content", counting_content)
    monkeypatch.setattr(engine_mod, "update_items_meta", counting_meta)

    drafts = [_eid_draft(f"Keep/{i}.json", text=f"body {i}") for i in range(n)]
    _run(ctx, drafts, batch_size=batch_size)
    # Second run: every draft hits the update/duplicate classification path.
    changed = [_eid_draft(f"Keep/{i}.json", text=f"body {i} edited") for i in range(n)]
    _run(ctx, changed, batch_size=batch_size)

    assert calls["existing_eid"] == 2 * expected_batches
    assert calls["update_content"] == 2 * expected_batches
    assert calls["update_meta"] == 2 * expected_batches


def test_parse_overlaps_inflight_write(ctx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """#199 pipelining: the engine pulls (parses) batch N+1 while batch N's
    write is still executing on the writer thread — deterministically proven
    by blocking the first write until the generator reaches batch 2."""
    import threading

    write_started = threading.Event()
    release_write = threading.Event()
    write_calls: list[int] = []
    real_write_batch = engine_mod._write_batch

    def gated_write(conn: sqlite3.Connection, **kwargs: object) -> list[str]:
        write_calls.append(1)
        if len(write_calls) == 1:
            write_started.set()
            assert release_write.wait(timeout=10), "batch 2 was never pulled during write 1"
        return real_write_batch(conn, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(engine_mod, "_write_batch", gated_write)

    def drafts() -> Iterator[NoteDraft]:
        for i in range(4):
            if i == 3:  # inside batch 2's pull (batch_size=2)
                assert write_started.wait(timeout=10)
                release_write.set()
            yield NoteDraft(title=f"t{i}", text=f"unique body {i}")

    import_id = run_import(
        ctx.db,
        source_name="test-src",
        parser_version=1,
        drafts=drafts(),
        path="/tmp/test.zip",
        file_hash=None,
        batch_size=2,
    )

    with ctx.db.read() as conn:
        count = int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
        status = conn.execute("SELECT status FROM imports WHERE id = ?", (import_id,)).fetchone()[
            "status"
        ]
    assert count == 4
    assert status == "completed"


def test_bulk_pragmas_active_during_import_and_restored(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#199 rider: batch writes run under the bulk cache/checkpoint pragmas,
    restored when the import finishes. synchronous stays NORMAL throughout —
    OFF plus running checkpoints risks whole-DB corruption on power loss
    (#198 review 12)."""
    observed: list[tuple[int, int]] = []
    real_write_batch = engine_mod._write_batch

    def spying_write(conn: sqlite3.Connection, **kwargs: object) -> list[str]:
        observed.append(
            (
                int(conn.execute("PRAGMA synchronous").fetchone()[0]),
                int(conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]),
            )
        )
        return real_write_batch(conn, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(engine_mod, "_write_batch", spying_write)
    _run(ctx, _make_drafts(5))

    assert observed, "no batches written"
    assert all(sync == 1 for sync, _ in observed), f"synchronous must stay NORMAL: {observed}"
    assert all(ckpt == 10000 for _, ckpt in observed), f"bulk checkpoints expected: {observed}"
    after = ctx.db.write(lambda c: int(c.execute("PRAGMA wal_autocheckpoint").fetchone()[0]))
    assert after == 1000, "standard checkpoint cadence must be restored after the import"


def test_bulk_pragmas_restored_after_failed_import(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exploding_write(conn: sqlite3.Connection, **kwargs: object) -> list[str]:
        raise sqlite3.OperationalError("disk I/O error (synthetic)")

    monkeypatch.setattr(engine_mod, "_write_batch", exploding_write)
    with pytest.raises(sqlite3.OperationalError):
        _run(ctx, _make_drafts(5))

    after = ctx.db.write(lambda c: int(c.execute("PRAGMA synchronous").fetchone()[0]))
    assert after == 1, "synchronous=NORMAL must be restored after a failed import"


def test_in_batch_displacement_then_revert_across_batches(ctx: AppContext) -> None:
    """batch 1 = [x/C1, x/C2] (C2 displaces C1 in-batch), batch 2 = [x/C1]:
    the displaced draft's hash must leave the seen set when it is shadowed,
    or the revert is misclassified as an in-run duplicate and the row stays
    at C2 (#198 review 17). The outcome must not depend on batch boundaries."""
    drafts = [
        _eid_draft("Keep/r.json", text="version-one"),
        _eid_draft("Keep/r.json", text="version-two"),
        _eid_draft("Keep/r.json", text="version-one"),
    ]
    import_id = _run(ctx, drafts, batch_size=2)

    with ctx.db.read() as conn:
        rows = conn.execute("SELECT text FROM items").fetchall()

    assert [r["text"] for r in rows] == ["version-one"]
    assert _import_counters(ctx, import_id) == (1, 1, 1)


def test_in_batch_displacement_then_revert_same_batch(ctx: AppContext) -> None:
    """All three drafts in ONE batch: last-wins must also hold there."""
    drafts = [
        _eid_draft("Keep/r.json", text="version-one"),
        _eid_draft("Keep/r.json", text="version-two"),
        _eid_draft("Keep/r.json", text="version-one"),
    ]
    _run(ctx, drafts, batch_size=1000)

    with ctx.db.read() as conn:
        rows = conn.execute("SELECT text FROM items").fetchall()

    assert [r["text"] for r in rows] == ["version-one"]
