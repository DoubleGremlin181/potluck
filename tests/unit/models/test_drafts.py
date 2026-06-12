"""EmailDraft DTO (#123)."""

import pytest
from pydantic import ValidationError

from potluck.models.drafts import EmailAttachment, EmailDraft
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
