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

    # Field-boundary safety: \x1f separator prevents ("ab","c") == ("a","bc")
    d_boundary1 = _note(title="ab", text="c")
    d_boundary2 = _note(title="a", text="bc")
    assert content_hash(d_boundary1) != content_hash(d_boundary2)


def test_file_hash_matches_hashlib(tmp_path: Path) -> None:
    content = b"hello world" * 1000
    p = tmp_path / "test.bin"
    p.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    assert file_hash(p) == expected
