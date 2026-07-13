"""Deterministic Google Chat Takeout generator (potluck.testing.google_chat)."""

import json
import re
from pathlib import Path

from potluck.testing.google_chat import (
    expected_duplicate_suffix_count,
    expected_media_reference_count,
    expected_message_count,
    google_chat_members,
    message_ts,
    write_google_chat_takeout,
)

_DM = "Takeout/Google Chat/Groups/DM synthdm01AAAE/messages.json"
_SPACE = "Takeout/Google Chat/Groups/Space AAAAsynthsp1/messages.json"

# The real created_date shape: full English weekday/month names, non-padded
# day and hour, padded minutes/seconds, a NARROW NO-BREAK SPACE (U+202F)
# before the meridiem (as on every real message), UTC token.
_CREATED_SHAPE = re.compile(
    "^[A-Z][a-z]+, [A-Z][a-z]+ \\d{1,2}, \\d{4} at \\d{1,2}:\\d{2}:\\d{2}\u202f[AP]M UTC$"
)


def _records(members: dict[str, bytes], member: str = _DM) -> list[dict[str, object]]:
    doc = json.loads(members[member])
    assert isinstance(doc, dict)
    records = doc["messages"]
    assert isinstance(records, list)
    return records


def test_same_arguments_produce_identical_bytes() -> None:
    assert google_chat_members(24, seed=5) == google_chat_members(24, seed=5)


def test_different_seeds_produce_different_messages() -> None:
    assert google_chat_members(24, seed=5)[_DM] != google_chat_members(24, seed=6)[_DM]


def test_member_set_mirrors_real_export_layout() -> None:
    """The real Google Chat folder: Groups/<'DM '|'Space '><id>/ pairs of
    group_info.json + messages.json, a Users/ dir with user_info.json and
    unsentmessages.json, and File-* attachment blobs the parser never reads.
    One DM is empty (a real export carried three) — a legitimate silent state."""
    members = google_chat_members(4)
    assert sorted(members) == [
        "Takeout/Google Chat/Groups/DM synthdm01AAAE/File-synthetic-9.png",
        "Takeout/Google Chat/Groups/DM synthdm01AAAE/group_info.json",
        _DM,
        "Takeout/Google Chat/Groups/DM synthdm02AAAE/group_info.json",
        "Takeout/Google Chat/Groups/DM synthdm02AAAE/messages.json",
        "Takeout/Google Chat/Groups/Space AAAAsynthsp1/group_info.json",
        _SPACE,
        "Takeout/Google Chat/Users/User 000000000000000000042/unsentmessages.json",
        "Takeout/Google Chat/Users/User 000000000000000000042/user_info.json",
    ]


def test_record_key_orders_mirror_real_export() -> None:
    members = google_chat_members(24, seed=7)
    records = _records(members)
    # Plain text record (i=0), attachment record (i=9), membership stub (i=3):
    # field names and order exactly as the real 2025-12 export.
    assert list(records[0]) == ["creator", "created_date", "text", "topic_id", "message_id"]
    assert list(records[9]) == [
        "creator",
        "created_date",
        "attached_files",
        "topic_id",
        "message_id",
    ]
    assert list(records[3]) == ["creator", "created_date", "topic_id", "message_id"]
    creator = records[0]["creator"]
    assert isinstance(creator, dict)
    assert list(creator) == ["name", "email", "user_type"]
    assert creator["user_type"] == "Human"


def test_group_info_and_user_info_shapes() -> None:
    members = google_chat_members(4)
    dm_info = json.loads(members["Takeout/Google Chat/Groups/DM synthdm01AAAE/group_info.json"])
    assert list(dm_info) == ["members"]  # DMs carry no name key
    assert [list(m) for m in dm_info["members"]] == [["name", "email", "user_type"]] * 2
    space_info = json.loads(
        members["Takeout/Google Chat/Groups/Space AAAAsynthsp1/group_info.json"]
    )
    assert list(space_info) == ["name", "members"]  # spaces carry the room name first
    assert space_info["name"] == "Synthetic Fixture Crew"
    assert len(space_info["members"]) == 3
    user_info = json.loads(
        members["Takeout/Google Chat/Users/User 000000000000000000042/user_info.json"]
    )
    assert list(user_info) == ["user", "membership_info"]
    assert list(user_info["user"]) == ["name", "email", "user_type"]
    assert user_info["user"]["email"] == "ada@potluck.test"


def test_empty_dm_has_empty_messages_array() -> None:
    members = google_chat_members(24)
    assert _records(members, "Takeout/Google Chat/Groups/DM synthdm02AAAE/messages.json") == []


def test_created_date_renders_real_shape() -> None:
    records = _records(google_chat_members(40, seed=7))
    for record in records:
        created = record["created_date"]
        assert isinstance(created, str)
        assert _CREATED_SHAPE.match(created), created
    # message_ts(0) is the base instant, rendered non-padded like the real export.
    assert message_ts(0).isoformat() == "2023-03-17T09:00:00+00:00"
    assert records[0]["created_date"] == "Friday, March 17, 2023 at 9:00:00\u202fAM UTC"


def test_message_ids_embed_group_topic_and_message_segments() -> None:
    records = _records(google_chat_members(8, seed=7))
    mid = records[0]["message_id"]
    assert isinstance(mid, str)
    group_segment, topic_segment, _ = mid.split("/")
    assert group_segment == "synthdm01AAAE"  # the dir's opaque id, sans "DM "
    assert topic_segment == records[0]["topic_id"]


def test_shapes_match_closed_forms() -> None:
    count = 40
    records = _records(google_chat_members(count, seed=7))
    assert len(records) == count
    # Duplicates are verbatim copies of their predecessor (defensive #N path).
    dups = [i for i in range(1, count) if records[i] == records[i - 1]]
    assert len(dups) == expected_duplicate_suffix_count(count)
    memberships = [r for r in records if "text" not in r and "attached_files" not in r]
    attached = [r for r in records if "attached_files" in r]
    assert len(records) - len(memberships) == expected_message_count(count)
    assert len(attached) == expected_media_reference_count(count)
    for record in attached:
        files = record["attached_files"]
        assert isinstance(files, list)
        assert [list(f) for f in files] == [["export_name", "original_name"]]
    # Multi-line and emoji content are exercised (golden fixture guarantees).
    texts = [t for r in records if isinstance(t := r.get("text"), str)]
    assert any("\n" in t for t in texts)
    assert any(any(ord(ch) > 0x1F000 for ch in t) for t in texts)


def test_space_and_dm_render_different_content() -> None:
    members = google_chat_members(24, seed=7)
    dm_texts = [r.get("text") for r in _records(members)]
    space_texts = [r.get("text") for r in _records(members, _SPACE)]
    assert dm_texts != space_texts


def test_write_dir_and_zip_formats(tmp_path: Path) -> None:
    root = write_google_chat_takeout(tmp_path, 8, seed=7, fmt="dir")
    assert root == tmp_path / "google-chat-synth-001"
    assert (root / _DM).is_file()
    archive = write_google_chat_takeout(tmp_path, 8, seed=7, fmt="zip")
    assert archive == tmp_path / "google-chat-synth-001.zip"
    assert archive.is_file()
