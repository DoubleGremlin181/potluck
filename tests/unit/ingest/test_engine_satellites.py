"""Engine satellite dispatch (#123): emails rows ride the same batch transaction."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from potluck.ingest.engine import run_import
from potluck.models.drafts import EmailDraft, ItemDraft, NoteDraft
from potluck.services.context import AppContext


def _email(n: int, *, labels: tuple[str, ...] = (), in_reply_to: str | None = None) -> EmailDraft:
    msgid = f"m{n}@potluck.test"
    return EmailDraft(
        external_id=f"mid:{msgid}",
        message_id=msgid,
        in_reply_to=in_reply_to,
        thread_key="root@potluck.test",
        from_addr=f"sender{n}@potluck.test",
        to_addrs=("to@potluck.test",),
        labels=labels,
        title=f"subject {n}",
        text=f"body {n}",
        ts=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=n),
    )


def _run(ctx: AppContext, drafts: list[ItemDraft], *, batch_size: int = 1000) -> int:
    return run_import(
        ctx.db,
        source_name="gmail-test",
        parser_version=1,
        drafts=iter(drafts),
        path="/tmp/test.mbox",
        file_hash=None,
        batch_size=batch_size,
    )


def test_import_writes_emails_satellite(ctx: AppContext) -> None:
    _run(ctx, [_email(1, labels=("Inbox",)), _email(2)])
    with ctx.db.read() as conn:
        rows = conn.execute(
            """SELECT e.message_id, e.thread_key, e.from_addr, e.labels_json
               FROM emails e JOIN items i ON i.id = e.item_id ORDER BY e.message_id"""
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["message_id"] == "m1@potluck.test"
    assert rows[0]["thread_key"] == "root@potluck.test"
    assert json.loads(rows[0]["labels_json"]) == ["Inbox"]


def test_notes_get_no_satellite_rows(ctx: AppContext) -> None:
    _run(ctx, [NoteDraft(title="note", text="text")])
    with ctx.db.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    assert count == 0


def test_label_change_updates_item_and_satellite(ctx: AppContext) -> None:
    """Labels are inside the content hash: a moved label is an UPDATE, and the
    satellite row is rewritten in the same run."""
    _run(ctx, [_email(1, labels=("Inbox",))])
    import_id2 = _run(ctx, [_email(1, labels=("Archived",))])

    with ctx.db.read() as conn:
        imp = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id2,)).fetchone()
        labels = conn.execute("SELECT labels_json FROM emails").fetchone()[0]
        item_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert item_count == 1
    assert int(imp["items_updated"]) == 1
    assert json.loads(labels) == ["Archived"]


def test_exact_reimport_skips_satellite_rewrite(ctx: AppContext) -> None:
    _run(ctx, [_email(1)])
    import_id2 = _run(ctx, [_email(1)])
    with ctx.db.read() as conn:
        imp = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id2,)).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    assert int(imp["items_duplicate"]) == 1
    assert count == 1


def test_parent_resolution_within_run_across_batches(ctx: AppContext) -> None:
    """batch_size=1 forces the reply and its parent into different batches;
    the end-of-run reconciliation must still link them — including the forward
    reference (reply stored before its parent)."""
    reply_first: list[ItemDraft] = [_email(2, in_reply_to="m1@potluck.test"), _email(1)]
    _run(ctx, reply_first, batch_size=1)

    with ctx.db.read() as conn:
        rows = conn.execute(
            """SELECT e.message_id, i.parent_id FROM emails e
               JOIN items i ON i.id = e.item_id"""
        ).fetchall()
        by_msgid = {str(r["message_id"]): r["parent_id"] for r in rows}
        parent_item = conn.execute(
            "SELECT e.item_id FROM emails e WHERE e.message_id = 'm1@potluck.test'"
        ).fetchone()[0]
    assert by_msgid["m1@potluck.test"] is None
    assert by_msgid["m2@potluck.test"] == parent_item


def test_parent_resolution_across_imports(ctx: AppContext) -> None:
    """A reply whose parent only arrives in a LATER import is linked by that
    later run's reconciliation."""
    _run(ctx, [_email(2, in_reply_to="m1@potluck.test")])
    _run(ctx, [_email(1)])

    with ctx.db.read() as conn:
        child_parent = conn.execute(
            """SELECT i.parent_id FROM emails e JOIN items i ON i.id = e.item_id
               WHERE e.message_id = 'm2@potluck.test'"""
        ).fetchone()[0]
        parent_item = conn.execute(
            "SELECT item_id FROM emails WHERE message_id = 'm1@potluck.test'"
        ).fetchone()[0]
    assert child_parent == parent_item


def test_batch_first_satellite_writes(ctx: AppContext, monkeypatch: object) -> None:
    """The engine calls the satellite writer ONCE per batch, not per draft."""
    import pytest

    from potluck.models.items import ItemKind
    from potluck.storage import satellites

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    calls: list[int] = []
    real = satellites.SATELLITE_WRITERS[ItemKind.EMAIL]

    def counting_write(conn: sqlite3.Connection, pairs: object) -> None:
        assert isinstance(pairs, list)
        calls.append(len(pairs))
        real.write_batch(conn, pairs)

    monkeypatch.setitem(
        satellites.SATELLITE_WRITERS,
        ItemKind.EMAIL,
        satellites.SatelliteWriter(write_batch=counting_write, finalize=real.finalize),
    )

    drafts: list[ItemDraft] = [_email(n) for n in range(1, 51)]
    _run(ctx, drafts, batch_size=25)

    assert calls == [25, 25]
    with ctx.db.read() as conn:
        pairs = conn.execute(
            "SELECT COUNT(*) FROM emails e JOIN items i ON i.id = e.item_id"
        ).fetchone()[0]
    assert pairs == 50
