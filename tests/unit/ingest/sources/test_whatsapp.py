"""Tests for the WhatsApp chat-export source plugin.

Testing private helpers (_parse_chat, _chat_identity, _infer_day_first) is
intentional: locale inference, system-message skipping, and the identity
policy are the public contract of this module and must be covered at the
unit level, from synthetic bytes.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from potluck.ingest.plugins import ParseContext, detect_sources, discover
from potluck.ingest.readers import open_archive
from potluck.ingest.sources.whatsapp import (
    _chat_identity,
    _infer_day_first,
    _parse_chat,
    parse,
)
from potluck.models.drafts import MessageDraft
from potluck.models.items import ItemKind
from potluck.services.context import AppContext
from potluck.services.imports import import_path
from potluck.testing.archives import write_archive

_MEMBER = "WhatsApp Chat with Ada Example.txt"


def _drafts(text: str, member: str = _MEMBER) -> list[MessageDraft]:
    counters: dict[str, int] = {}
    return list(_parse_chat(text.encode(), member, counters))


# ---------------------------------------------------------------------------
# Message lines: US 12h dash format (Android)
# ---------------------------------------------------------------------------


def test_us_dash_format_basic() -> None:
    drafts = _drafts("3/17/23, 9:05 AM - Ada Example: hello there\n")
    assert len(drafts) == 1
    d = drafts[0]
    assert d.kind is ItemKind.MESSAGE
    assert d.ts == datetime(2023, 3, 17, 9, 5, tzinfo=UTC)
    assert d.sender == "Ada Example"
    assert d.text == "hello there"
    assert d.title is None
    assert d.chat_key == "WhatsApp Chat with Ada Example"
    assert d.chat_name == "Ada Example"
    assert d.is_media is False
    assert d.media == ()


def test_us_pm_and_midnight_noon_hours() -> None:
    text = (
        "3/17/23, 12:01 AM - Ada Example: after midnight\n"
        "3/17/23, 9:05 PM - Ada Example: evening\n"
        "3/17/23, 12:30 PM - Ada Example: lunchtime\n"
    )
    hours = [d.ts.hour for d in _drafts(text) if d.ts is not None]
    assert hours == [0, 21, 12]


def test_lowercase_and_dotted_meridiem() -> None:
    text = "3/17/23, 9:05 p.m. - Ada Example: hi\n3/17/23, 9:06 a.m. - Ada Example: ho\n"
    drafts = _drafts(text)
    assert [d.ts.hour for d in drafts if d.ts is not None] == [21, 9]


# ---------------------------------------------------------------------------
# EU 24h day-first (Android) and separator variants
# ---------------------------------------------------------------------------


def test_eu_day_first_24h_slash() -> None:
    drafts = _drafts("17/03/2023, 14:05 - Bo Sample: hallo\n")
    assert drafts[0].ts == datetime(2023, 3, 17, 14, 5, tzinfo=UTC)


def test_eu_dotted_dates_two_digit_year() -> None:
    drafts = _drafts("17.03.23, 14:05 - Bo Sample: hallo\n")
    assert drafts[0].ts == datetime(2023, 3, 17, 14, 5, tzinfo=UTC)


def test_year_first_dates_are_unambiguous() -> None:
    drafts = _drafts("2023/03/05, 14:05 - Bo Sample: hallo\n")
    assert drafts[0].ts == datetime(2023, 3, 5, 14, 5, tzinfo=UTC)


# ---------------------------------------------------------------------------
# iOS bracket format
# ---------------------------------------------------------------------------


def test_ios_bracket_with_seconds_narrow_nbsp_and_lrm() -> None:
    line = "‎[3/17/23, 9:05:42\u202fAM] Ada Example: from an iphone\n"
    drafts = _drafts(line, member="WhatsApp Chat - Ada Example/_chat.txt")
    assert len(drafts) == 1
    d = drafts[0]
    assert d.ts == datetime(2023, 3, 17, 9, 5, 42, tzinfo=UTC)
    assert d.sender == "Ada Example"
    assert d.text == "from an iphone"
    assert d.chat_key == "WhatsApp Chat - Ada Example"
    assert d.chat_name == "Ada Example"


def test_ios_bracket_24h_no_meridiem() -> None:
    drafts = _drafts("[17/03/23, 21:05:42] Bo Sample: spät\n")
    assert drafts[0].ts == datetime(2023, 3, 17, 21, 5, 42, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Locale inference
# ---------------------------------------------------------------------------


def test_infer_day_first_from_first_decisive_date() -> None:
    assert _infer_day_first(["17/03/23, 14:05 - A: x"]) is True
    assert _infer_day_first(["3/17/23, 9:05 AM - A: x"]) is False


def test_ambiguous_dates_default_by_clock_style() -> None:
    # All components <= 12: AM/PM present -> month-first (US)
    assert _infer_day_first(["3/4/23, 9:05 AM - A: x"]) is False
    # 24h clock -> day-first (rest of the world)
    assert _infer_day_first(["3/4/23, 14:05 - A: x"]) is True


def test_inference_applies_to_earlier_ambiguous_lines() -> None:
    """The decisive date can come late; earlier lines still parse day-first."""
    text = "05/03/2023, 14:00 - A: ambiguous\n17/03/2023, 14:05 - A: decisive\n"
    drafts = _drafts(text)
    assert drafts[0].ts == datetime(2023, 3, 5, 14, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# System messages: skipped, never items
# ---------------------------------------------------------------------------


def test_system_messages_without_sender_are_skipped() -> None:
    text = (
        "3/17/23, 9:00 AM - Messages and calls are end-to-end encrypted. "
        "Tap to learn more.\n"
        '3/17/23, 9:01 AM - Ada Example created group "Trip Planning"\n'
        "3/17/23, 9:02 AM - Bo Sample joined using this group's invite link\n"
        "3/17/23, 9:03 AM - Ada Example: a real message\n"
    )
    drafts = _drafts(text)
    assert [d.text for d in drafts] == ["a real message"]


def test_ios_sender_attributed_encryption_notice_is_skipped() -> None:
    """iOS attributes the encryption notice to a sender with a leading LRM —
    the body pattern must still classify it as system chrome."""
    text = (
        "[3/17/23, 9:00:01\u202fAM] Ada Example: ‎Messages and calls are "
        "end-to-end encrypted. No one outside of this chat, not even "
        "WhatsApp, can read or listen to them.\n"
        "[3/17/23, 9:01:00\u202fAM] Ada Example: real content\n"
    )
    drafts = _drafts(text)
    assert [d.text for d in drafts] == ["real content"]


# ---------------------------------------------------------------------------
# Multi-line messages
# ---------------------------------------------------------------------------


def test_multiline_message_concatenates_continuation_lines() -> None:
    text = (
        "3/17/23, 9:05 AM - Ada Example: first line\n"
        "second line\n"
        "\n"
        "fourth line after a blank\n"
        "3/17/23, 9:06 AM - Bo Sample: next message\n"
    )
    drafts = _drafts(text)
    assert len(drafts) == 2
    assert drafts[0].text == "first line\nsecond line\n\nfourth line after a blank"
    assert drafts[1].text == "next message"


def test_leading_junk_before_first_prefix_is_ignored() -> None:
    drafts = _drafts("not a chat line\n3/17/23, 9:05 AM - Ada Example: hi\n")
    assert [d.text for d in drafts] == ["hi"]


# ---------------------------------------------------------------------------
# Media placeholders
# ---------------------------------------------------------------------------


def test_media_omitted_placeholder() -> None:
    drafts = _drafts("3/17/23, 9:05 AM - Ada Example: <Media omitted>\n")
    d = drafts[0]
    assert d.is_media is True
    assert d.text is None
    assert d.media == ()


def test_android_file_attached_records_media_reference() -> None:
    drafts = _drafts("3/17/23, 9:05 AM - Ada Example: IMG-20230317-WA0001.jpg (file attached)\n")
    d = drafts[0]
    assert d.is_media is True
    assert d.text is None
    assert len(d.media) == 1
    assert d.media[0].filename == "IMG-20230317-WA0001.jpg"
    assert d.media[0].mime == "image/jpeg"


def test_media_caption_on_continuation_lines_becomes_text() -> None:
    text = "3/17/23, 9:05 AM - Ada Example: IMG-20230317-WA0001.jpg (file attached)\nthe caption\n"
    d = _drafts(text)[0]
    assert d.is_media is True
    assert d.media[0].filename == "IMG-20230317-WA0001.jpg"
    assert d.text == "the caption"


def test_ios_attached_and_omitted_variants() -> None:
    text = (
        "[3/17/23, 9:05:00\u202fAM] Ada Example: ‎<attached: "
        "00000012-PHOTO-2023-03-17-09-05-00.jpg>\n"
        "[3/17/23, 9:06:00\u202fAM] Ada Example: ‎image omitted\n"
        "[3/17/23, 9:07:00\u202fAM] Ada Example: ‎video omitted\n"
    )
    drafts = _drafts(text)
    assert [d.is_media for d in drafts] == [True, True, True]
    assert drafts[0].media[0].filename == "00000012-PHOTO-2023-03-17-09-05-00.jpg"
    assert drafts[1].media == drafts[2].media == ()


def test_message_mentioning_media_words_is_not_media() -> None:
    d = _drafts("3/17/23, 9:05 AM - Ada Example: the image omitted half the story\n")[0]
    assert d.is_media is False
    assert d.text == "the image omitted half the story"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_external_id_is_raw_block_fingerprint() -> None:
    line = "3/17/23, 9:05 AM - Ada Example: hello there"
    d = _drafts(line + "\n")[0]
    expected = hashlib.sha256(f"WhatsApp Chat with Ada Example\x1e{line}".encode()).hexdigest()
    assert d.external_id == f"wa:{expected}"


def test_identical_blocks_get_occurrence_suffixes() -> None:
    line = "3/17/23, 9:05 AM - Ada Example: ok\n"
    drafts = _drafts(line * 3)
    eids = [d.external_id for d in drafts]
    assert eids[0] is not None and eids[0].startswith("wa:")
    assert eids[1] == f"{eids[0]}#2"
    assert eids[2] == f"{eids[0]}#3"


def test_same_text_in_different_chats_gets_different_ids() -> None:
    line = "3/17/23, 9:05 AM - Ada Example: ok\n"
    a = _drafts(line, member="WhatsApp Chat with Ada Example.txt")[0]
    b = _drafts(line, member="WhatsApp Chat with Bo Sample.txt")[0]
    assert a.external_id != b.external_id


def test_identity_is_deterministic_across_parses() -> None:
    text = (
        "3/17/23, 9:05 AM - Ada Example: hello\nsecond line\n"
        "3/17/23, 9:06 AM - Bo Sample: <Media omitted>\n"
    )
    assert [d.external_id for d in _drafts(text)] == [d.external_id for d in _drafts(text)]


# ---------------------------------------------------------------------------
# Containment and edge cases
# ---------------------------------------------------------------------------


def test_invalid_date_skips_message_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    text = "31/02/2023, 14:05 - Bo Sample: impossible date\n17/03/2023, 14:06 - Bo Sample: fine\n"
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(text)
    assert [d.text for d in drafts] == ["fine"]
    assert any("skipping" in r.message for r in caplog.records)


def test_unrecognized_dialect_warns_and_yields_nothing(
    ctx: AppContext, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A detected chat file whose lines match no timestamp dialect (e.g. a
    French "17/03/2023 à 14:05 -" export) must log one WARNING naming the
    member — never a silent zero-item import — and the run still completes."""
    french = "17/03/2023 à 14:05 - Ada Example: bonjour\n17/03/2023 à 14:06 - Bo Sample: salut\n"
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(french)
    assert drafts == []
    assert any("no line has a recognizable timestamp header" in r.message for r in caplog.records)

    archive = write_archive(
        tmp_path / "export.zip", {"WhatsApp Chat with Ada Example.txt": french.encode()}, "zip"
    )
    [run] = import_path(ctx, archive)
    assert run.status == "completed"
    assert run.items_new == 0


def test_empty_detected_member_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert _drafts("") == []
        assert _drafts("\n\n") == []
    assert not caplog.records


def test_nbsp_in_first_line_body_is_preserved() -> None:
    """The prefix matches on a space-normalized copy, but rest slices from
    the original line: a body NBSP must survive on the first line exactly as
    it would on a continuation line."""
    text = "3/17/23, 9:05\u202fAM - Ada Example: price 10\u00a0EUR\nline\u00a0two\n"
    d = _drafts(text)[0]
    assert d.text == "price 10\u00a0EUR\nline\u00a0two"
    assert d.sender == "Ada Example"


def test_bom_and_crlf_are_handled() -> None:
    text = "﻿3/17/23, 9:05 AM - Ada Example: hi\r\n3/17/23, 9:06 AM - Bo Sample: ho\r\n"
    drafts = _drafts(text)
    assert [d.text for d in drafts] == ["hi", "ho"]


def test_empty_file_yields_nothing() -> None:
    assert _drafts("") == []


def test_emoji_and_rtl_text_survive() -> None:
    text = "3/17/23, 9:05 AM - Ada Example: نص عربي שלום 🎉🚀\n"
    assert _drafts(text)[0].text == "نص عربي שלום 🎉🚀"


def test_empty_sender_segment_is_system() -> None:
    drafts = _drafts("3/17/23, 9:05 AM - : orphaned colon\n")
    assert drafts == []


# ---------------------------------------------------------------------------
# Chat identity anchors
# ---------------------------------------------------------------------------


def test_chat_identity_anchors() -> None:
    assert _chat_identity("WhatsApp Chat with Ada Example.txt") == (
        "WhatsApp Chat with Ada Example",
        "Ada Example",
    )
    assert _chat_identity("nested/dir/WhatsApp Chat with Ada Example.txt") == (
        "WhatsApp Chat with Ada Example",
        "Ada Example",
    )
    assert _chat_identity("WhatsApp Chat - Ada Example/_chat.txt") == (
        "WhatsApp Chat - Ada Example",
        "Ada Example",
    )
    assert _chat_identity("_chat.txt") == ("_chat", "_chat")


# ---------------------------------------------------------------------------
# Detection + parse() over real archives
# ---------------------------------------------------------------------------


def test_detection_matches_export_shapes_precisely(tmp_path: Path) -> None:
    matches = {
        "WhatsApp Chat with Ada Example.txt": True,
        "backup/WhatsApp Chat with Ada Example.txt": True,
        "_chat.txt": True,
        "WhatsApp Chat - Ada Example/_chat.txt": True,
        # precision: the generic text ingester's territory (#150)
        "notes.txt": False,
        "my_chat.txt": False,
        "chat.txt": False,
        "WhatsApp Chat with Ada Example.txt.gpg": False,
    }
    plugin = discover()["whatsapp"]
    for name, expected in matches.items():
        assert plugin.detect.matches(name) is expected, name

    archive = write_archive(
        tmp_path / "export.zip",
        {"WhatsApp Chat with Ada Example.txt": b"3/17/23, 9:05 AM - Ada Example: hi\n"},
        "zip",
    )
    assert [p.name for p in detect_sources(open_archive(archive))] == ["whatsapp"]


def test_parse_reads_only_chat_members(tmp_path: Path) -> None:
    members = {
        "WhatsApp Chat with Ada Example.txt": b"3/17/23, 9:05 AM - Ada Example: hi\n",
        "WhatsApp Chat - Bo Sample/_chat.txt": (
            "[3/17/23, 9:06:00\u202fAM] Bo Sample: yo\n".encode()
        ),
        "WhatsApp Chat - Bo Sample/00000001-PHOTO-2023-03-17-09-06-00.jpg": b"\xff\xd8\xff",
        "notes.txt": b"3/17/23, 9:07 AM - Decoy Person: never parsed\n",
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    drafts = list(parse(open_archive(archive), ParseContext()))
    assert all(isinstance(d, MessageDraft) for d in drafts)
    assert sorted(d.text for d in drafts if d.text) == ["hi", "yo"]
    assert {d.chat_key for d in drafts if isinstance(d, MessageDraft)} == {
        "WhatsApp Chat with Ada Example",
        "WhatsApp Chat - Bo Sample",
    }


def test_parse_empty_archive_yields_nothing(tmp_path: Path) -> None:
    archive = write_archive(tmp_path / "empty.zip", {"decoy/readme.md": b"x"}, "zip")
    assert list(parse(open_archive(archive), ParseContext())) == []
