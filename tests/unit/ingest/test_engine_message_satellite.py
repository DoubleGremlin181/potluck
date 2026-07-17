"""Engine satellite dispatch for messages (#142): messages rows + media file
references ride the same batch transaction, and item detail hydrates them."""

from datetime import UTC, datetime, timedelta

from potluck.models.drafts import MessageDraft, MessageMedia
from potluck.services.context import AppContext
from potluck.services.items import get_item
from potluck.services.threads import get_thread
from tests.conftest import ingest_email_drafts


def _message(
    n: int,
    *,
    chat_key: str = "WhatsApp Chat with Ada Example",
    text: str | None = None,
    is_media: bool = False,
    media: tuple[MessageMedia, ...] = (),
) -> MessageDraft:
    return MessageDraft(
        external_id=f"wa:fp{n}",
        ts=datetime(2023, 3, 17, 9, 0, tzinfo=UTC) + timedelta(minutes=n),
        text=text if text is not None else f"message {n}",
        chat_key=chat_key,
        chat_name="Ada Example",
        sender="Ada Example",
        is_media=is_media,
        media=media,
    )


def _run(ctx: AppContext, *drafts: MessageDraft) -> int:
    return ingest_email_drafts(ctx, *drafts, source_name="whatsapp", path="/tmp/export.zip")


def test_import_writes_messages_satellite(ctx: AppContext) -> None:
    _run(ctx, _message(1), _message(2))
    with ctx.db.read() as conn:
        rows = conn.execute(
            """SELECT m.chat_key, m.chat_name, m.sender, m.is_media
               FROM messages m JOIN items i ON i.id = m.item_id ORDER BY i.id"""
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["chat_key"] == "WhatsApp Chat with Ada Example"
    assert rows[0]["chat_name"] == "Ada Example"
    assert rows[0]["sender"] == "Ada Example"
    assert rows[0]["is_media"] == 0


def test_media_references_land_in_files(ctx: AppContext) -> None:
    media = (MessageMedia(filename="IMG-20230317-WA0001.jpg", mime="image/jpeg"),)
    _run(ctx, _message(1, text="caption", is_media=True, media=media))
    with ctx.db.read() as conn:
        rows = conn.execute("SELECT member_path, mime, size_bytes, sha256 FROM files").fetchall()
    assert len(rows) == 1
    assert rows[0]["member_path"] == "IMG-20230317-WA0001.jpg"
    assert rows[0]["mime"] == "image/jpeg"
    assert rows[0]["size_bytes"] is None  # metadata only; pixels deferred to P6
    assert rows[0]["sha256"] is None


def test_exact_reimport_is_duplicate(ctx: AppContext) -> None:
    _run(ctx, _message(1))
    import_id2 = _run(ctx, _message(1))
    with ctx.db.read() as conn:
        imp = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id2,)).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert int(imp["items_duplicate"]) == 1
    assert count == 1


def test_satellite_change_reingests_as_update(ctx: AppContext) -> None:
    """Satellite fields live inside the content hash (extra_hash_parts), so a
    sender-only change must be an UPDATE that rewrites the satellite row."""
    _run(ctx, _message(1))
    changed = _message(1).model_copy(update={"sender": "Bo Sample"})
    import_id2 = _run(ctx, changed)

    with ctx.db.read() as conn:
        imp = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id2,)).fetchone()
        sender = conn.execute("SELECT sender FROM messages").fetchone()[0]
        item_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert item_count == 1
    assert int(imp["items_updated"]) == 1
    assert sender == "Bo Sample"


def test_media_set_replaced_wholesale_on_update(ctx: AppContext) -> None:
    media = (MessageMedia(filename="IMG-20230317-WA0001.jpg", mime="image/jpeg"),)
    _run(ctx, _message(1, is_media=True, media=media))
    replacement = (MessageMedia(filename="VID-20230317-WA0002.mp4", mime="video/mp4"),)
    _run(ctx, _message(1, is_media=True, media=replacement))
    with ctx.db.read() as conn:
        paths = [str(r[0]) for r in conn.execute("SELECT member_path FROM files").fetchall()]
    assert paths == ["VID-20230317-WA0002.mp4"]


def test_get_item_hydrates_message_detail(ctx: AppContext) -> None:
    media = (MessageMedia(filename="IMG-20230317-WA0001.jpg", mime="image/jpeg"),)
    _run(ctx, _message(1, text="caption", is_media=True, media=media))
    with ctx.db.read() as conn:
        item_id = int(conn.execute("SELECT id FROM items").fetchone()[0])

    item = get_item(ctx, item_id)
    assert item.email is None
    assert item.message is not None
    assert item.message.chat_key == "WhatsApp Chat with Ada Example"
    assert item.message.chat_name == "Ada Example"
    assert item.message.sender == "Ada Example"
    assert item.message.is_media is True
    assert [m.filename for m in item.message.media] == ["IMG-20230317-WA0001.jpg"]


def test_get_thread_for_message_is_item_level_for_now(ctx: AppContext) -> None:
    """Chat-scale threads need windowing before get_thread can serve them
    (a chat is one thread with up to ~100k members); until then a message
    resolves like any non-email item — a single-entry thread."""
    _run(ctx, _message(1), _message(2))
    with ctx.db.read() as conn:
        item_id = int(conn.execute("SELECT id FROM items ORDER BY id").fetchone()[0])
    thread = get_thread(ctx, item_id)
    assert thread.thread_key is None
    assert [e.id for e in thread.entries] == [item_id]
