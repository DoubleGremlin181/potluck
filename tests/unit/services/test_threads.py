"""Thread retrieval service (#123): ordered conversations from the emails satellite."""

from datetime import UTC, datetime

import pytest

from potluck.core.errors import ItemNotFoundError
from potluck.ingest.engine import run_import
from potluck.models.drafts import EmailDraft, ItemDraft, NoteDraft
from potluck.services.context import AppContext
from potluck.services.threads import get_thread


def _email(
    n: int,
    *,
    thread_key: str = "root@potluck.test",
    in_reply_to: str | None = None,
    ts_hour: int | None = None,
) -> EmailDraft:
    msgid = f"m{n}@potluck.test"
    return EmailDraft(
        external_id=f"mid:{msgid}",
        message_id=msgid,
        in_reply_to=in_reply_to,
        thread_key=thread_key,
        from_addr=f"sender{n}@potluck.test",
        title=f"subject {n}",
        text=f"body {n}",
        ts=datetime(2024, 1, 1, ts_hour, tzinfo=UTC) if ts_hour is not None else None,
    )


def _ingest(ctx: AppContext, drafts: list[ItemDraft]) -> None:
    run_import(
        ctx.db,
        source_name="gmail-test",
        parser_version=1,
        drafts=iter(drafts),
        path="/tmp/test.mbox",
        file_hash=None,
    )


def _item_id(ctx: AppContext, message_id: str) -> int:
    with ctx.db.read() as conn:
        row = conn.execute(
            "SELECT item_id FROM emails WHERE message_id = ?", (message_id,)
        ).fetchone()
    return int(row[0])


def test_get_thread_returns_ordered_conversation(ctx: AppContext) -> None:
    _ingest(
        ctx,
        [
            _email(2, in_reply_to="m1@potluck.test", ts_hour=2),
            _email(1, ts_hour=1),
            _email(3, in_reply_to="m2@potluck.test", ts_hour=3),
            _email(9, thread_key="other@potluck.test", ts_hour=4),
        ],
    )
    anchor = _item_id(ctx, "m2@potluck.test")
    thread = get_thread(ctx, anchor)

    assert thread.item_id == anchor
    assert thread.thread_key == "root@potluck.test"
    assert [e.title for e in thread.entries] == ["subject 1", "subject 2", "subject 3"]
    # parent_id forms the reply tree
    ids = {e.title: e.id for e in thread.entries}
    parents = {e.title: e.parent_id for e in thread.entries}
    assert parents["subject 1"] is None
    assert parents["subject 2"] == ids["subject 1"]
    assert parents["subject 3"] == ids["subject 2"]
    assert thread.entries[0].from_addr == "sender1@potluck.test"


def test_get_thread_undated_members_sort_last(ctx: AppContext) -> None:
    _ingest(ctx, [_email(1, ts_hour=1), _email(2, ts_hour=None)])
    thread = get_thread(ctx, _item_id(ctx, "m1@potluck.test"))
    assert [e.title for e in thread.entries] == ["subject 1", "subject 2"]


def test_get_thread_non_email_returns_single_entry(ctx: AppContext) -> None:
    _ingest(ctx, [NoteDraft(title="a note", text="content")])
    with ctx.db.read() as conn:
        note_id = int(conn.execute("SELECT id FROM items").fetchone()[0])
    thread = get_thread(ctx, note_id)
    assert thread.thread_key is None
    assert [e.title for e in thread.entries] == ["a note"]
    assert thread.entries[0].id == note_id


def test_get_thread_unknown_id_raises(ctx: AppContext) -> None:
    with pytest.raises(ItemNotFoundError):
        get_thread(ctx, 99999)


def test_get_thread_scoped_to_source(ctx: AppContext) -> None:
    """Identical thread_keys in different sources are different conversations."""
    _ingest(ctx, [_email(1, ts_hour=1)])
    run_import(
        ctx.db,
        source_name="other-src",
        parser_version=1,
        drafts=iter([_email(5, ts_hour=2)]),
        path="/tmp/other.mbox",
        file_hash=None,
    )
    thread = get_thread(ctx, _item_id(ctx, "m1@potluck.test"))
    assert [e.title for e in thread.entries] == ["subject 1"]
