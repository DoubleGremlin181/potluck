"""Standalone mbox source plugin (#150): gmail's parsing/identity recipe under
the ``mbox:`` namespace, run-wide Message-ID bookkeeping, containment, tier.
"""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

import pytest

import potluck.ingest.sources.mbox as mbox_source
from potluck.ingest.mbox import parse_email as real_parse_email
from potluck.ingest.plugins import ParseContext, detect_sources, discover
from potluck.ingest.readers import Member, open_archive
from potluck.models.drafts import EmailDraft
from potluck.testing.archives import write_archive

_MSG_A = (
    b"Message-ID: <a1@potluck.test>\n"
    b"Date: Mon, 01 Jan 2024 08:00:00 +0000\n"
    b"From: Ada Example <ada@potluck.test>\n"
    b"To: bo@potluck.test\n"
    b"Subject: synthetic hello\n"
    b"\n"
    b"hello body\n"
)
_MSG_B = (
    b"Message-ID: <b2@potluck.test>\n"
    b"Date: Mon, 01 Jan 2024 09:00:00 +0000\n"
    b"From: Bo Sample <bo@potluck.test>\n"
    b"To: ada@potluck.test\n"
    b"In-Reply-To: <a1@potluck.test>\n"
    b"References: <a1@potluck.test>\n"
    b"Subject: Re: synthetic hello\n"
    b"\n"
    b"reply body\n"
)
_MSG_NOID = (
    b"Date: Mon, 01 Jan 2024 10:00:00 +0000\n"
    b"From: Cy Test <cy@potluck.test>\n"
    b"To: ada@potluck.test\n"
    b"Subject: no message id\n"
    b"\n"
    b"anonymous body\n"
)


def _mbox(*messages: bytes) -> bytes:
    envelope = b"From ada@potluck.test Mon Jan  1 08:00:00 2024\n"
    return b"".join(envelope + m + b"\n" for m in messages)


def _parse_file(path: Path) -> list[EmailDraft]:
    drafts = list(mbox_source.parse(open_archive(path), ParseContext()))
    return [d for d in drafts if isinstance(d, EmailDraft)]  # narrows; parse yields only these


# ---------------------------------------------------------------------------
# Parsing + identity
# ---------------------------------------------------------------------------


def test_bare_mbox_file_parses_end_to_end(tmp_path: Path) -> None:
    """A standalone foo.mbox (SingleFileArchive) is a real import shape."""
    path = tmp_path / "archive-2024.mbox"
    path.write_bytes(_mbox(_MSG_A, _MSG_B))
    drafts = _parse_file(path)
    assert [d.external_id for d in drafts] == [
        "mbox:mid:a1@potluck.test",
        "mbox:mid:b2@potluck.test",
    ]
    first, reply = drafts
    assert first.title == "synthetic hello"
    assert first.text == "hello body\n\n"  # the mbox entry separator line joins the body
    assert first.ts == datetime(2024, 1, 1, 8, 0, tzinfo=UTC)
    assert first.from_addr == "ada@potluck.test"
    assert first.from_name == "Ada Example"
    assert first.to_addrs == ("bo@potluck.test",)
    assert first.thread_key == "a1@potluck.test"
    assert reply.in_reply_to == "a1@potluck.test"
    assert reply.thread_key == "a1@potluck.test"  # References root


def test_missing_message_id_gets_namespaced_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "one.mbox"
    path.write_bytes(_mbox(_MSG_NOID))
    [draft] = _parse_file(path)
    assert draft.external_id is not None
    assert draft.external_id.startswith("mbox:noid:")
    assert draft.thread_key == draft.external_id


def test_duplicate_msgid_identical_bytes_share_identity(tmp_path: Path) -> None:
    path = tmp_path / "dup.mbox"
    path.write_bytes(_mbox(_MSG_A, _MSG_A))
    drafts = _parse_file(path)
    assert len(drafts) == 2
    assert drafts[0].external_id == drafts[1].external_id == "mbox:mid:a1@potluck.test"


def test_duplicate_msgid_different_body_gets_suffix(tmp_path: Path) -> None:
    variant = _MSG_A.replace(b"hello body", b"different body")
    path = tmp_path / "dup.mbox"
    path.write_bytes(_mbox(_MSG_A, variant))
    drafts = _parse_file(path)
    assert [d.external_id for d in drafts] == [
        "mbox:mid:a1@potluck.test",
        "mbox:mid:a1@potluck.test#2",
    ]


def test_msgid_bookkeeping_spans_members(tmp_path: Path) -> None:
    """A folder of mbox files (Thunderbird-style per-folder exports): the
    same message appearing in two files collides on external_id and dedups —
    the gmail run-wide posture."""
    folder = tmp_path / "mail-backup"
    write_archive(
        folder,
        {"inbox.mbox": _mbox(_MSG_A, _MSG_B), "archive.mbox": _mbox(_MSG_A)},
        "dir",
    )
    drafts = _parse_file(folder)
    assert len(drafts) == 3
    # DirArchive iterates sorted: archive.mbox (A) first, then inbox.mbox (A, B).
    assert drafts[0].external_id == drafts[1].external_id == "mbox:mid:a1@potluck.test"


def test_gmail_and_mbox_drafts_agree_except_namespace(tmp_path: Path) -> None:
    """The extraction proof: the same raw message through gmail's Mail layout
    and through a standalone mbox yields identical drafts apart from the
    ``mbox:`` identity prefix (and they stay two items per-schema)."""
    from potluck.ingest.sources.gmail import parse as gmail_parse

    takeout = write_archive(
        tmp_path / "takeout.zip", {"Takeout/Mail/All mail.mbox": _mbox(_MSG_A)}, "zip"
    )
    [gmail_draft] = list(gmail_parse(open_archive(takeout), ParseContext(workers=1)))

    bare = tmp_path / "standalone.mbox"
    bare.write_bytes(_mbox(_MSG_A))
    [mbox_draft] = _parse_file(bare)

    assert mbox_draft.external_id == f"mbox:{gmail_draft.external_id}"
    assert mbox_draft.model_dump(exclude={"external_id"}) == gmail_draft.model_dump(
        exclude={"external_id"}
    )


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------


def test_corrupt_message_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def poisoned(raw: bytes, **kwargs: object) -> object:
        if b"no message id" in raw:
            raise ValueError("synthetic decode failure")
        return real_parse_email(raw)

    monkeypatch.setattr(mbox_source, "parse_email", poisoned)
    path = tmp_path / "mixed.mbox"
    path.write_bytes(_mbox(_MSG_A, _MSG_NOID, _MSG_B))
    with caplog.at_level(logging.WARNING):
        drafts = _parse_file(path)
    assert [d.title for d in drafts] == ["synthetic hello", "Re: synthetic hello"]
    warnings = [r.message for r in caplog.records if r.name.startswith("potluck")]
    assert len(warnings) == 1
    assert "mixed.mbox" in warnings[0]


# ---------------------------------------------------------------------------
# Detection tier
# ---------------------------------------------------------------------------


class _FakeArchive:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def iter_names(self) -> Iterator[str]:
        yield from self._names

    def iter_members(self, pattern: str) -> Iterator[tuple[Member, IO[bytes]]]:
        return iter([])


def test_mail_layout_still_belongs_to_gmail() -> None:
    """Under tier fallback a Mail/-structured archive goes to gmail alone;
    a standalone mbox (no specific match) reaches the generic tier."""
    plugin = discover()["mbox"]
    assert plugin.generic is True

    mail_layout = _FakeArchive(["Takeout/Mail/All mail Including Spam and Trash.mbox"])
    assert [p.name for p in detect_sources(mail_layout)] == ["gmail"]

    standalone = _FakeArchive(["backup/archive-2024.mbox"])
    assert [p.name for p in detect_sources(standalone)] == ["mbox"]
