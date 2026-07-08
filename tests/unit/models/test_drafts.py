"""EmailDraft DTO (#123), MessageDraft DTO (#142), PostDraft/BookmarkDraft (#143)."""

import pytest
from pydantic import ValidationError

from potluck.ingest.hashing import content_hash
from potluck.models.drafts import (
    BookmarkDraft,
    EmailAttachment,
    EmailDraft,
    MessageDraft,
    MessageMedia,
    PostDraft,
)
from potluck.models.items import ItemKind


def test_email_draft_defaults() -> None:
    draft = EmailDraft(thread_key="tk")
    assert draft.kind is ItemKind.EMAIL
    assert draft.message_id is None
    assert draft.in_reply_to is None
    assert draft.to_addrs == ()
    assert draft.cc_addrs == ()
    assert draft.labels == ()
    assert draft.attachments == ()


def test_email_draft_is_frozen() -> None:
    draft = EmailDraft(thread_key="tk")
    with pytest.raises(ValidationError):
        draft.from_addr = "x@potluck.test"


def test_email_draft_full() -> None:
    att = EmailAttachment(
        filename="a.bin",
        mime="application/octet-stream",
        size_bytes=12,
        sha256="ab" * 32,
    )
    draft = EmailDraft(
        external_id="mid:one@potluck.test",
        message_id="one@potluck.test",
        in_reply_to="zero@potluck.test",
        thread_key="zero@potluck.test",
        from_addr="alice@potluck.test",
        to_addrs=("bob@potluck.test",),
        cc_addrs=("carol@example.com",),
        labels=("Inbox", "Unread"),
        attachments=(att,),
        title="subject",
        text="body",
    )
    assert draft.attachments[0].sha256 == "ab" * 32
    assert draft.labels == ("Inbox", "Unread")


def test_message_draft_defaults() -> None:
    draft = MessageDraft(chat_key="chat")
    assert draft.kind is ItemKind.MESSAGE
    assert draft.chat_name is None
    assert draft.sender is None
    assert draft.is_media is False
    assert draft.media == ()


def test_message_draft_is_frozen() -> None:
    draft = MessageDraft(chat_key="chat")
    with pytest.raises(ValidationError):
        draft.sender = "Ada Example"


def test_post_draft_defaults_and_kind() -> None:
    draft = PostDraft(title="a title", text="a body")
    assert draft.kind is ItemKind.POST
    assert draft.external_id is None
    assert draft.meta == {}
    assert draft.extra_hash_parts() == ()


def test_bookmark_draft_defaults_and_kind() -> None:
    draft = BookmarkDraft(title="a saved thing")
    assert draft.kind is ItemKind.BOOKMARK
    assert draft.ts is None
    assert draft.text is None
    assert draft.extra_hash_parts() == ()


def test_post_and_bookmark_drafts_are_frozen() -> None:
    post = PostDraft(text="x")
    bookmark = BookmarkDraft(title="y")
    with pytest.raises(ValidationError):
        post.text = "changed"
    with pytest.raises(ValidationError):
        bookmark.title = "changed"


def test_kind_disambiguates_content_hash() -> None:
    """Same base fields under different kinds must never hash-collide (the
    kind is part of the canonical identity)."""
    assert content_hash(PostDraft(text="same")) != content_hash(BookmarkDraft(text="same"))


def test_message_extra_hash_parts_cover_every_satellite_field() -> None:
    """The extra_hash_parts invariant: any change to a satellite-persisted
    field (messages row or media files rows) must change the content hash,
    or re-ingests would dedup the change away as 'unchanged'."""
    base = MessageDraft(
        chat_key="chat",
        chat_name="Ada Example",
        sender="Ada Example",
        is_media=True,
        media=(MessageMedia(filename="a.jpg", mime="image/jpeg"),),
        text="caption",
    )
    variants = [
        base.model_copy(update={"chat_key": "other"}),
        base.model_copy(update={"chat_name": "Bo Sample"}),
        base.model_copy(update={"sender": "Bo Sample"}),
        base.model_copy(update={"is_media": False}),
        base.model_copy(update={"media": (MessageMedia(filename="b.jpg"),)}),
        base.model_copy(update={"media": (MessageMedia(filename="a.jpg", mime="image/png"),)}),
    ]
    hashes = {content_hash(d) for d in [base, *variants]}
    assert len(hashes) == len(variants) + 1
