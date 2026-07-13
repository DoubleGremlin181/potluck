"""Tests for the Google Chat Takeout source plugin.

Testing private helpers (_parse_messages, _parse_created_date, _chat_name,
_parse_group_info, _parse_user_info) is intentional: timestamp parsing, the
sidecar join, non-content skipping, and the identity policy are the public
contract of this module and must be covered at the unit level, from
synthetic bytes.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from potluck.ingest.plugins import ParseContext, detect_sources, discover
from potluck.ingest.readers import open_archive
from potluck.ingest.sources.google_chat import (
    _chat_name,
    _group_dir,
    _GroupInfo,
    _parse_created_date,
    _parse_group_info,
    _parse_messages,
    _parse_user_info,
    parse,
)
from potluck.models.drafts import MessageDraft
from potluck.models.items import ItemKind
from potluck.testing.archives import write_archive

_MEMBER = "Takeout/Google Chat/Groups/DM synthdm01AAAE/messages.json"
_CHAT_KEY = "DM synthdm01AAAE"


def _record(i: int = 0, **overrides: object) -> dict[str, object]:
    """One well-formed real-shape record; overrides replace or (None) drop keys."""
    record: dict[str, object] = {
        "creator": {"name": "Ada Example", "email": "ada@potluck.test", "user_type": "Human"},
        "created_date": "Friday, March 17, 2023 at 9:05:00 AM UTC",
        "text": f"synthetic message {i}",
        "topic_id": f"syntopic-{i:04d}",
        "message_id": f"synthdm01AAAE/syntopic-{i:04d}/synmsg-{i:04d}",
    }
    for key, value in overrides.items():
        if value is None:
            record.pop(key, None)
        else:
            record[key] = value
    return record


def _drafts(
    records: list[dict[str, object]],
    member: str = _MEMBER,
    chat_name: str | None = "Bo Sample",
) -> list[MessageDraft]:
    data = json.dumps({"messages": records}).encode()
    return list(_parse_messages(data, member, _group_dir(member), chat_name))


# ---------------------------------------------------------------------------
# Message fields
# ---------------------------------------------------------------------------


def test_basic_message_fields() -> None:
    [d] = _drafts([_record()])
    assert d.kind is ItemKind.MESSAGE
    assert d.external_id == "gchat:synthdm01AAAE/syntopic-0000/synmsg-0000"
    assert d.ts == datetime(2023, 3, 17, 9, 5, tzinfo=UTC)
    assert d.text == "synthetic message 0"
    assert d.title is None
    assert d.chat_key == _CHAT_KEY
    assert d.chat_name == "Bo Sample"
    assert d.sender == "Ada Example"
    assert d.is_media is False
    assert d.media == ()
    assert d.meta == {"sender_email": "ada@potluck.test"}


def test_multiline_and_emoji_text_survive_verbatim() -> None:
    text = "first line\nsecond line\n\nnach der Leerzeile 🎉🚀"
    [d] = _drafts([_record(text=text)])
    assert d.text == text


def test_sender_falls_back_to_email_without_name() -> None:
    [d] = _drafts([_record(creator={"email": "bo@potluck.test", "user_type": "Human"})])
    assert d.sender == "bo@potluck.test"
    assert d.meta == {"sender_email": "bo@potluck.test"}


def test_missing_creator_yields_no_sender_and_no_meta() -> None:
    [d] = _drafts([_record(creator=None)])
    assert d.sender is None
    assert d.meta == {}


# ---------------------------------------------------------------------------
# created_date parsing
# ---------------------------------------------------------------------------


def test_created_date_parses_the_real_shape() -> None:
    """The real export puts a NARROW NO-BREAK SPACE (U+202F) before AM/PM —
    on every one of the real messages — but plain-space renderings must parse
    identically (the whatsapp iOS normalization)."""
    parsed = _parse_created_date("Thursday, March 14, 2024 at 10:30:15\u202fPM UTC")
    assert parsed == (datetime(2024, 3, 14, 22, 30, 15, tzinfo=UTC), "UTC")
    assert _parse_created_date("Thursday, March 14, 2024 at 10:30:15 PM UTC") == parsed


def test_created_date_noon_and_midnight() -> None:
    noon = _parse_created_date("Friday, March 17, 2023 at 12:00:00 PM UTC")
    midnight = _parse_created_date("Friday, March 17, 2023 at 12:00:00 AM UTC")
    assert noon is not None and noon[0].hour == 12
    assert midnight is not None and midnight[0].hour == 0


def test_created_date_single_digit_day_and_hour() -> None:
    parsed = _parse_created_date("Sunday, July 2, 2023 at 8:05:09 AM UTC")
    assert parsed == (datetime(2023, 7, 2, 8, 5, 9, tzinfo=UTC), "UTC")


def test_created_date_rejects_foreign_shapes() -> None:
    assert _parse_created_date("2023-03-17T09:05:00Z") is None
    assert _parse_created_date("Freitag, März 17, 2023 at 9:05:00 AM UTC") is None
    assert _parse_created_date("Friday, March 32, 2023 at 9:05:00 AM UTC") is None  # ValueError
    assert _parse_created_date("") is None


def test_unparseable_created_date_warns_once_and_keeps_messages(
    caplog: pytest.LogCaptureFixture,
) -> None:
    records = [
        _record(0, created_date="17.03.2023, 09:05"),
        _record(1, created_date="17.03.2023, 09:06"),
        _record(2),
    ]
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(records)
    assert [d.ts for d in drafts] == [None, None, datetime(2023, 3, 17, 9, 5, tzinfo=UTC)]
    assert sum("created_date" in r.message for r in caplog.records) == 1


def test_missing_created_date_counts_as_unparseable(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        [d] = _drafts([_record(created_date=None)])
    assert d.ts is None
    assert d.text == "synthetic message 0"
    assert sum("created_date" in r.message for r in caplog.records) == 1


def test_non_utc_timezone_token_reads_as_utc_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real export renders UTC on every message; a foreign token is taken
    AS UTC (the whatsapp/gmail unknown-zone policy) and warned once per member."""
    records = [
        _record(0, created_date="Friday, March 17, 2023 at 9:05:00 AM CET"),
        _record(1, created_date="Friday, March 17, 2023 at 9:06:00 AM CET"),
    ]
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(records)
    assert [d.ts for d in drafts] == [
        datetime(2023, 3, 17, 9, 5, tzinfo=UTC),
        datetime(2023, 3, 17, 9, 6, tzinfo=UTC),
    ]
    assert sum("CET" in r.message for r in caplog.records) == 1


# ---------------------------------------------------------------------------
# Attachments and annotations
# ---------------------------------------------------------------------------


def test_attachment_only_message() -> None:
    files = [{"export_name": "File-synthetic-9.png", "original_name": "synthetic-9.png"}]
    [d] = _drafts([_record(text=None, attached_files=files)])
    assert d.is_media is True
    assert d.text is None
    assert len(d.media) == 1
    assert d.media[0].filename == "File-synthetic-9.png"  # export_name locates the blob (P6)
    assert d.media[0].mime == "image/png"


def test_attachment_falls_back_to_original_name() -> None:
    [d] = _drafts([_record(text=None, attached_files=[{"original_name": "synthetic.gif"}])])
    assert d.media[0].filename == "synthetic.gif"
    assert d.media[0].mime == "image/gif"


def test_attachment_with_text_keeps_both() -> None:
    files = [{"export_name": "File-synthetic.jpg", "original_name": "synthetic.jpg"}]
    [d] = _drafts([_record(attached_files=files)])
    assert d.is_media is True
    assert d.text == "synthetic message 0"
    assert d.media[0].filename == "File-synthetic.jpg"


def test_unusable_attachment_entries_are_dropped() -> None:
    files: list[object] = ["not-a-dict", {"export_name": ""}, {}]
    drafts = _drafts([_record(text=None, attached_files=files)])
    assert drafts == []  # no usable media and no text → non-content


def test_annotations_are_ignored() -> None:
    annotations = [
        {"start_index": 0, "length": 9, "url_metadata": {"url": {}, "title": "synthetic"}},
        {"start_index": 0, "length": 9, "youtube_metadata": {"id": "syn", "start_time": 0}},
    ]
    [d] = _drafts([_record(text="synthetic https://www.example.com", annotations=annotations)])
    assert d.text == "synthetic https://www.example.com"
    assert d.meta == {"sender_email": "ada@potluck.test"}


# ---------------------------------------------------------------------------
# Non-content records (system/membership stubs)
# ---------------------------------------------------------------------------


def test_record_without_text_or_attachments_is_skipped_silently(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        drafts = _drafts([_record(text=None), _record(1)])
    assert [d.text for d in drafts] == ["synthetic message 1"]
    assert not caplog.records


def test_empty_text_is_non_content() -> None:
    assert _drafts([_record(text="")]) == []


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_duplicate_message_ids_get_occurrence_suffixes() -> None:
    drafts = _drafts([_record(), _record(), _record()])
    eids = [d.external_id for d in drafts]
    assert eids[0] == "gchat:synthdm01AAAE/syntopic-0000/synmsg-0000"
    assert eids[1] == f"{eids[0]}#2"
    assert eids[2] == f"{eids[0]}#3"


def test_counters_are_member_scoped_so_reexport_copies_collide() -> None:
    """Two members carrying the same group are re-exports of the same chat:
    their copies must mint IDENTICAL ids (dedup), never #2 (double-import)."""
    a = _drafts([_record()])
    b = _drafts(
        [_record()], member="part2/Takeout/Google Chat/Groups/DM synthdm01AAAE/messages.json"
    )
    assert a[0].external_id == b[0].external_id


def test_missing_message_id_falls_back_to_composite(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        [d] = _drafts([_record(message_id=None)])
    eid = d.external_id
    assert eid is not None and eid.startswith("gchat:") and "/" not in eid
    assert any("message_id" in r.message for r in caplog.records)
    # Deterministic across parses…
    with caplog.at_level(logging.WARNING):
        [again] = _drafts([_record(message_id=None)])
    assert again.external_id == eid
    # …and sensitive to chat and content (verbatim exported values).
    other_chat = _drafts(
        [_record(message_id=None)],
        member="Takeout/Google Chat/Groups/DM synthdm02AAAE/messages.json",
    )
    other_text = _drafts([_record(message_id=None, text="different synthetic text")])
    assert other_chat[0].external_id != eid
    assert other_text[0].external_id != eid


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------


def test_malformed_json_member_warns_and_yields_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        drafts = list(_parse_messages(b'{"messages": [', _MEMBER, _CHAT_KEY, None))
    assert drafts == []
    assert any("JSON" in r.message for r in caplog.records)


def test_member_without_messages_array_warns(caplog: pytest.LogCaptureFixture) -> None:
    for payload in (b'{"other": []}', b'{"messages": {}}', b"[]"):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            assert list(_parse_messages(payload, _MEMBER, _CHAT_KEY, None)) == []
        assert any('"messages"' in r.message for r in caplog.records), payload


def test_empty_messages_array_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert _drafts([]) == []
    assert not caplog.records


def test_non_dict_record_warns_and_skips(caplog: pytest.LogCaptureFixture) -> None:
    records: list[dict[str, object]] = [_record()]
    data = json.dumps({"messages": ["not-a-record", *records]}).encode()
    with caplog.at_level(logging.WARNING):
        drafts = list(_parse_messages(data, _MEMBER, _CHAT_KEY, None))
    assert [d.text for d in drafts] == ["synthetic message 0"]
    assert any("not an object" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Sidecars: group_info.json + user_info.json → chat_name
# ---------------------------------------------------------------------------


def _info(*members: tuple[str | None, str | None], name: str | None = None) -> _GroupInfo:
    return _GroupInfo(name=name, members=tuple(members))


def test_parse_group_info_dm_and_space_shapes() -> None:
    dm = _parse_group_info(
        json.dumps(
            {
                "members": [
                    {"name": "Ada Example", "email": "ada@potluck.test", "user_type": "Human"},
                    {"name": "Bo Sample", "email": "bo@potluck.test", "user_type": "Human"},
                ]
            }
        ).encode(),
        "Takeout/Google Chat/Groups/DM synthdm01AAAE/group_info.json",
    )
    assert dm == _info(
        ("Ada Example", "ada@potluck.test"), ("Bo Sample", "bo@potluck.test"), name=None
    )
    space = _parse_group_info(
        json.dumps({"name": "Synthetic Fixture Crew", "members": []}).encode(),
        "Takeout/Google Chat/Groups/Space AAAAsynthsp1/group_info.json",
    )
    assert space == _info(name="Synthetic Fixture Crew")


def test_malformed_group_info_warns_and_degrades(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        info = _parse_group_info(b"{broken", "g/group_info.json")
    assert info == _info(name=None)
    assert any("group_info" in r.message for r in caplog.records)


def test_parse_user_info_owner_email() -> None:
    data = json.dumps(
        {
            "user": {"name": "Ada Example", "email": "ada@potluck.test", "user_type": "Human"},
            "membership_info": [
                {"group_id": "DM synthdm01AAAE", "membership_state": "MEMBER_JOINED"}
            ],
        }
    ).encode()
    assert _parse_user_info(data, "u/user_info.json") == "ada@potluck.test"


def test_malformed_user_info_warns_and_returns_none(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert _parse_user_info(b"{broken", "u/user_info.json") is None
        assert _parse_user_info(b'{"user": {}}', "u/user_info.json") is None
    assert any("user_info" in r.message for r in caplog.records)


def test_chat_name_dm_is_the_other_participant() -> None:
    info = _info(("Ada Example", "ada@potluck.test"), ("Bo Sample", "bo@potluck.test"))
    assert _chat_name(info, "ada@potluck.test") == "Bo Sample"
    # Email comparison is case-insensitive (Google normalizes case freely).
    assert _chat_name(info, "Ada@Potluck.Test") == "Bo Sample"


def test_chat_name_unknown_owner_joins_all_members() -> None:
    info = _info(("Ada Example", "ada@potluck.test"), ("Bo Sample", "bo@potluck.test"))
    assert _chat_name(info, None) == "Ada Example, Bo Sample"


def test_chat_name_group_dm_joins_the_other_participants() -> None:
    info = _info(
        ("Ada Example", "ada@potluck.test"),
        ("Bo Sample", "bo@potluck.test"),
        ("Cy Test", "cy@potluck.test"),
    )
    assert _chat_name(info, "ada@potluck.test") == "Bo Sample, Cy Test"


def test_chat_name_space_uses_room_name() -> None:
    info = _info(("Ada Example", "ada@potluck.test"), name="Synthetic Fixture Crew")
    assert _chat_name(info, "ada@potluck.test") == "Synthetic Fixture Crew"


def test_chat_name_member_without_name_falls_back_to_email() -> None:
    info = _info(("Ada Example", "ada@potluck.test"), (None, "bo@potluck.test"))
    assert _chat_name(info, "ada@potluck.test") == "bo@potluck.test"


def test_chat_name_missing_sidecar_is_none() -> None:
    assert _chat_name(None, "ada@potluck.test") is None
    assert _chat_name(_info(), "ada@potluck.test") is None


def test_group_dir_anchor() -> None:
    assert _group_dir(_MEMBER) == "DM synthdm01AAAE"
    assert _group_dir("Google Chat/Groups/Space AAAAsynthsp1/group_info.json") == (
        "Space AAAAsynthsp1"
    )


# ---------------------------------------------------------------------------
# Detection + parse() over real archives
# ---------------------------------------------------------------------------


def test_detection_matches_export_shapes_precisely(tmp_path: Path) -> None:
    matches = {
        "Takeout/Google Chat/Groups/DM synthdm01AAAE/messages.json": True,
        "Google Chat/Groups/Space AAAAsynthsp1/messages.json": True,
        "re-zipped/Takeout/Google Chat/Groups/DM x/messages.json": True,
        # precision: sidecars and Users/ members are never message sources
        "Takeout/Google Chat/Groups/DM synthdm01AAAE/group_info.json": False,
        "Takeout/Google Chat/Users/User 42/user_info.json": False,
        "Takeout/Google Chat/Users/User 42/unsentmessages.json": False,
        "Takeout/Google Chat/Groups/messages.json": False,
        "messages.json": False,
        "Groups/DM synthdm01AAAE/messages.json": False,
        "Takeout/Hangouts/Groups/DM x/messages.json": False,
    }
    plugin = discover()["google_chat"]
    assert plugin.kinds == (ItemKind.MESSAGE,)
    for name, expected in matches.items():
        assert plugin.detect.matches(name) is expected, name

    archive = write_archive(
        tmp_path / "export.zip",
        {_MEMBER: json.dumps({"messages": []}).encode()},
        "zip",
    )
    assert [p.name for p in detect_sources(open_archive(archive))] == ["google_chat"]


def test_parse_joins_sidecars_regardless_of_archive_order(tmp_path: Path) -> None:
    """The real export interleaves group_info.json both before AND after its
    sibling messages.json; the sidecar pass makes the join order-independent.
    Decoy members (Users/ files, attachment blobs, a foreign messages.json)
    are never read as messages."""
    dm_dir = "Takeout/Google Chat/Groups/DM synthdm01AAAE"
    space_dir = "Takeout/Google Chat/Groups/Space AAAAsynthsp1"
    members = {
        # messages BEFORE the DM's own group_info (real archive order)
        f"{dm_dir}/messages.json": json.dumps({"messages": [_record()]}).encode(),
        f"{dm_dir}/File-synthetic-9.png": b"\x89PNG\r\n" + b"\x00" * 8,
        f"{space_dir}/group_info.json": json.dumps(
            {
                "name": "Synthetic Fixture Crew",
                "members": [
                    {"name": "Ada Example", "email": "ada@potluck.test", "user_type": "Human"},
                    {"name": "Bo Sample", "email": "bo@potluck.test", "user_type": "Human"},
                    {"name": "Cy Test", "email": "cy@potluck.test", "user_type": "Human"},
                ],
            }
        ).encode(),
        f"{space_dir}/messages.json": json.dumps(
            {"messages": [_record(1, message_id="AAAAsynthsp1/syntopic-0001/synmsg-0001")]}
        ).encode(),
        f"{dm_dir}/group_info.json": json.dumps(
            {
                "members": [
                    {"name": "Ada Example", "email": "ada@potluck.test", "user_type": "Human"},
                    {"name": "Bo Sample", "email": "bo@potluck.test", "user_type": "Human"},
                ]
            }
        ).encode(),
        "Takeout/Google Chat/Users/User 42/user_info.json": json.dumps(
            {
                "user": {"name": "Ada Example", "email": "ada@potluck.test", "user_type": "Human"},
                "membership_info": [],
            }
        ).encode(),
        "Takeout/Google Chat/Users/User 42/unsentmessages.json": json.dumps(
            {"unsent_messages": [{"text": "never imported"}]}
        ).encode(),
        "Other Product/messages.json": json.dumps(
            {"messages": [_record(2, text="decoy — never imported")]}
        ).encode(),
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    raw = list(parse(open_archive(archive), ParseContext()))
    assert all(isinstance(d, MessageDraft) for d in raw)
    drafts = [d for d in raw if isinstance(d, MessageDraft)]  # narrows for mypy
    by_key = {d.chat_key: d for d in drafts}
    assert set(by_key) == {"DM synthdm01AAAE", "Space AAAAsynthsp1"}
    assert by_key["DM synthdm01AAAE"].chat_name == "Bo Sample"
    assert by_key["Space AAAAsynthsp1"].chat_name == "Synthetic Fixture Crew"
    assert all(d.text != "never imported" for d in drafts)


def test_parse_without_sidecars_still_imports_messages(tmp_path: Path) -> None:
    archive = write_archive(
        tmp_path / "export.zip",
        {_MEMBER: json.dumps({"messages": [_record()]}).encode()},
        "zip",
    )
    [d] = list(parse(open_archive(archive), ParseContext()))
    assert isinstance(d, MessageDraft)
    assert d.chat_key == "DM synthdm01AAAE"
    assert d.chat_name is None
    assert d.text == "synthetic message 0"


def test_parse_empty_archive_yields_nothing(tmp_path: Path) -> None:
    archive = write_archive(tmp_path / "empty.zip", {"decoy/readme.md": b"x"}, "zip")
    assert list(parse(open_archive(archive), ParseContext())) == []
