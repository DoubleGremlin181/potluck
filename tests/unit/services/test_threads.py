"""Thread retrieval service (#123): ordered conversations from the emails satellite."""

import pytest

from potluck.core.errors import ItemNotFoundError
from potluck.models.drafts import NoteDraft
from potluck.services.context import AppContext
from potluck.services.threads import get_thread
from tests.conftest import email_draft, email_item_id, ingest_email_drafts

_ROOT = "root@potluck.test"


def test_get_thread_returns_ordered_conversation(ctx: AppContext) -> None:
    ingest_email_drafts(
        ctx,
        email_draft(2, thread_key=_ROOT, in_reply_to="m1@potluck.test"),
        email_draft(1, thread_key=_ROOT),
        email_draft(3, thread_key=_ROOT, in_reply_to="m2@potluck.test"),
        email_draft(9, thread_key="other@potluck.test"),
    )
    anchor = email_item_id(ctx, "m2@potluck.test")
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
    ingest_email_drafts(
        ctx, email_draft(1, thread_key=_ROOT), email_draft(2, thread_key=_ROOT, ts=None)
    )
    thread = get_thread(ctx, email_item_id(ctx, "m1@potluck.test"))
    assert [e.title for e in thread.entries] == ["subject 1", "subject 2"]


def test_get_thread_non_email_returns_single_entry(ctx: AppContext) -> None:
    ingest_email_drafts(ctx, NoteDraft(title="a note", text="content"))
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
    ingest_email_drafts(ctx, email_draft(1, thread_key=_ROOT))
    ingest_email_drafts(
        ctx,
        email_draft(5, thread_key=_ROOT),
        source_name="other-src",
        path="/tmp/other.mbox",
    )
    thread = get_thread(ctx, email_item_id(ctx, "m1@potluck.test"))
    assert [e.title for e in thread.entries] == ["subject 1"]
