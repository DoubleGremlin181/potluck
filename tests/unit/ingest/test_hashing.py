"""Tests for potluck.ingest.hashing: content_hash and file_hash."""

import hashlib
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from potluck.ingest.hashing import content_hash, file_hash
from potluck.models.drafts import NoteDraft


def _note(**kwargs: object) -> NoteDraft:
    return NoteDraft(**kwargs)  # type: ignore[arg-type]


def test_content_hash_deterministic() -> None:
    draft = _note(title="hello", text="world")
    h1 = content_hash(draft)
    h2 = content_hash(draft)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex digest is 64 chars


def test_content_hash_nfc_normalized() -> None:
    # "é" as NFC (precomposed U+00E9) vs NFD (e + combining accent U+0301)
    nfc_text = unicodedata.normalize("NFC", "café")
    nfd_text = unicodedata.normalize("NFD", "café")
    assert nfc_text != nfd_text, "sanity: NFC and NFD must be distinct byte sequences"

    d_nfc = _note(text=nfc_text)
    d_nfd = _note(text=nfd_text)
    assert content_hash(d_nfc) == content_hash(d_nfd)


def test_content_hash_ignores_meta() -> None:
    d1 = _note(title="same", meta={"key": "value1"})
    d2 = _note(title="same", meta={"key": "value2"})
    assert content_hash(d1) == content_hash(d2)


def test_content_hash_sensitive_to_fields() -> None:
    base = _note(title="hello", text="world")

    # external_id changes hash
    d_ext = _note(title="hello", text="world", external_id="x")
    assert content_hash(base) != content_hash(d_ext)

    # ts changes hash
    d_ts = _note(title="hello", text="world", ts=datetime(2020, 1, 1, tzinfo=UTC))
    assert content_hash(base) != content_hash(d_ts)

    # title changes hash
    d_title = _note(title="HELLO", text="world")
    assert content_hash(base) != content_hash(d_title)

    # text changes hash
    d_text = _note(title="hello", text="WORLD")
    assert content_hash(base) != content_hash(d_text)

    # Field-boundary safety: ("ab","c") must differ from ("a","bc")
    d_boundary1 = _note(title="ab", text="c")
    d_boundary2 = _note(title="a", text="bc")
    assert content_hash(d_boundary1) != content_hash(d_boundary2)

    # lat/lon change hash: a coordinate-only edit is a content change, not a dup
    d_geo = _note(title="hello", text="world", lat=37.7749, lon=-122.4194)
    d_geo2 = _note(title="hello", text="world", lat=37.7749, lon=-122.4195)
    assert content_hash(base) != content_hash(d_geo)
    assert content_hash(d_geo) != content_hash(d_geo2)


def test_content_hash_injective_against_separator_in_values() -> None:
    """The encoding must be injective even when field values contain the
    separator byte: a crafted title can't shift content across the title/text
    boundary and collide (reachable for external_id-less drafts)."""
    d1 = _note(title="a\x1fb", text="c")
    d2 = _note(title="a", text="b\x1fc")
    assert content_hash(d1) != content_hash(d2)


def test_file_hash_matches_hashlib(tmp_path: Path) -> None:
    content = b"hello world" * 1000
    p = tmp_path / "test.bin"
    p.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    assert file_hash(p) == expected


# ---------------------------------------------------------------------------
# extra_hash_parts (#123): satellite content participates in the hash
# ---------------------------------------------------------------------------

# Pinned P1 Keep hash: extra_hash_parts must be a pure extension — an empty
# tuple appends NOTHING, or every existing Keep item re-ingests as updated.
_PINNED_KEEP_HASH = "337f91bb67cc2a30c7b04c2c915934bccb7907bbb3f2e46a67f9347f7ef74c09"


def test_keep_hash_pinned_across_extra_parts_change() -> None:
    from datetime import UTC, datetime

    draft = NoteDraft(
        external_id="Keep/note.json",
        ts=datetime(2021, 5, 4, 12, 0, tzinfo=UTC),
        title="Amber Basil",
        text="Cedar dahlia ember.",
        meta={"labels": ["Work"]},
    )
    assert content_hash(draft) == _PINNED_KEEP_HASH


def test_email_label_change_changes_hash() -> None:
    from potluck.models.drafts import EmailDraft

    base = EmailDraft(thread_key="tk", title="s", text="b", labels=("Inbox",))
    moved = EmailDraft(thread_key="tk", title="s", text="b", labels=("Archived",))
    assert content_hash(base) != content_hash(moved)


def test_email_to_vs_cc_distinct_hash() -> None:
    """Variable-length groups must not collide across field boundaries."""
    from potluck.models.drafts import EmailDraft

    to_only = EmailDraft(thread_key="tk", to_addrs=("x@potluck.test",))
    cc_only = EmailDraft(thread_key="tk", cc_addrs=("x@potluck.test",))
    assert content_hash(to_only) != content_hash(cc_only)


def test_email_attachment_change_changes_hash() -> None:
    from potluck.models.drafts import EmailAttachment, EmailDraft

    att1 = EmailAttachment(filename="a", mime="text/plain", size_bytes=1, sha256="aa" * 32)
    att2 = EmailAttachment(filename="a", mime="text/plain", size_bytes=1, sha256="bb" * 32)
    d1 = EmailDraft(thread_key="tk", attachments=(att1,))
    d2 = EmailDraft(thread_key="tk", attachments=(att2,))
    assert content_hash(d1) != content_hash(d2)


def test_email_thread_key_changes_hash() -> None:
    from potluck.models.drafts import EmailDraft

    d1 = EmailDraft(thread_key="a", title="s")
    d2 = EmailDraft(thread_key="b", title="s")
    assert content_hash(d1) != content_hash(d2)
