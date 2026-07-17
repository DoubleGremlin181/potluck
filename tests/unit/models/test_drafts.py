"""EmailDraft DTO (#123), MessageDraft DTO (#142), PostDraft/BookmarkDraft
(#143), TransactionDraft (#144), EventDraft (#146)."""

import pytest
from pydantic import ValidationError

from potluck.ingest.hashing import content_hash
from potluck.models.drafts import (
    BookmarkDraft,
    EmailAttachment,
    EmailDraft,
    EventDraft,
    MessageDraft,
    MessageMedia,
    PostDraft,
    TransactionDraft,
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


def test_event_draft_defaults_and_kind() -> None:
    draft = EventDraft(title="a meeting")
    assert draft.kind is ItemKind.EVENT
    assert draft.external_id is None
    assert draft.ts is None
    assert draft.meta == {}
    assert draft.extra_hash_parts() == ()  # no satellite — base fields only


def test_event_draft_is_frozen() -> None:
    draft = EventDraft(title="a meeting")
    with pytest.raises(ValidationError):
        draft.title = "changed"


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


def test_transaction_draft_defaults_and_kind() -> None:
    draft = TransactionDraft(amount_milliunits=-4990)
    assert draft.kind is ItemKind.TRANSACTION
    assert draft.account is None
    assert draft.payee is None
    assert draft.category is None
    assert draft.category_group is None


def test_transaction_draft_is_frozen() -> None:
    draft = TransactionDraft(amount_milliunits=-4990)
    with pytest.raises(ValidationError):
        draft.amount_milliunits = -5000


def test_transaction_amount_is_required_and_never_a_float() -> None:
    """Integer milliunits are the money discipline (#144): a missing amount
    or a float amount must be unconstructable, even an integral float."""
    with pytest.raises(ValidationError):
        TransactionDraft()  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        TransactionDraft.model_validate({"amount_milliunits": 4990.0})
    with pytest.raises(ValidationError):
        TransactionDraft.model_validate({"amount_milliunits": 49.9})


def test_transaction_extra_hash_parts_cover_every_satellite_field() -> None:
    """The extra_hash_parts invariant: any change to a satellite-persisted
    field (transactions row) must change the content hash, or re-ingests
    would dedup the change away as 'unchanged'."""
    base = TransactionDraft(
        amount_milliunits=-17500,
        account="Synth Checking",
        payee="Corner Bakery",
        category="Dining Out",
        category_group="Fun Money",
        text="team breakfast",
    )
    variants = [
        base.model_copy(update={"amount_milliunits": -17510}),
        base.model_copy(update={"amount_milliunits": 17500}),  # sign flip
        base.model_copy(update={"account": "Synth Savings"}),
        base.model_copy(update={"payee": "Corner Cafe"}),
        base.model_copy(update={"category": "Groceries"}),
        base.model_copy(update={"category_group": "Bills"}),
    ]
    hashes = {content_hash(d) for d in [base, *variants]}
    assert len(hashes) == len(variants) + 1
