"""Deterministic Calendar Takeout generator (potluck.testing.calendar)."""

import json
from pathlib import Path

from potluck.testing.calendar import (
    LIFE_MEMBER,
    ROTA_COPY_MEMBER,
    ROTA_MEMBER,
    calendar_members,
    expected_all_day_count,
    expected_cross_file_duplicate_count,
    expected_draft_count,
    expected_item_count,
    write_calendar_takeout,
)

_SETTINGS = "Takeout/Calendar/meet_settings.json"


def _uids(member: bytes) -> list[str]:
    return [
        line.removeprefix("UID:")
        for line in member.decode().split("\r\n")
        if line.startswith("UID:")
    ]


def test_same_arguments_produce_identical_bytes() -> None:
    assert calendar_members(12, seed=5) == calendar_members(12, seed=5)


def test_different_seeds_produce_different_calendars() -> None:
    assert calendar_members(12, seed=5)[LIFE_MEMBER] != calendar_members(12, seed=6)[LIFE_MEMBER]


def test_member_set_mirrors_real_export_shape() -> None:
    """The real Calendar folder: one .ics per calendar, duplicated
    subscription copies under (N)-suffixed names, one meet_settings.json
    sibling the parser must never read."""
    members = calendar_members(4)
    assert sorted(members) == sorted([LIFE_MEMBER, ROTA_MEMBER, ROTA_COPY_MEMBER, _SETTINGS])
    assert LIFE_MEMBER == "Takeout/Calendar/Synthetic Life.ics"
    assert ROTA_COPY_MEMBER == "Takeout/Calendar/Synthetic Rota(1).ics"
    decoy = json.loads(members[_SETTINGS])
    assert "Meeting data" in decoy


def test_ics_lines_are_crlf_and_folded_within_75_octets() -> None:
    """RFC 5545 discipline: CRLF terminators, no physical line over 75
    octets, and the deliberately overlong summary actually folds."""
    for name, member in calendar_members(8).items():
        if not name.endswith(".ics"):
            continue
        text = member.decode()
        assert "\n" not in text.replace("\r\n", ""), name  # CRLF only
        for line in text.split("\r\n"):
            assert len(line.encode()) <= 75, (name, line)
    life = calendar_members(8)[LIFE_MEMBER].decode()
    assert any(line.startswith(" ") for line in life.split("\r\n"))  # a folded line


def test_escaped_commas_and_newlines_present() -> None:
    life = calendar_members(8)[LIFE_MEMBER]
    assert b"\\," in life
    assert b"\\n" in life
    assert b"\\;" in life


def test_event_counts_match_closed_forms() -> None:
    count = 20
    members = calendar_members(count, seed=7)
    life_events = members[LIFE_MEMBER].count(b"BEGIN:VEVENT")
    rota_events = members[ROTA_MEMBER].count(b"BEGIN:VEVENT")
    copy_events = members[ROTA_COPY_MEMBER].count(b"BEGIN:VEVENT")
    assert life_events + rota_events + copy_events == expected_draft_count(count)
    assert rota_events + 1 == copy_events  # the copy carries one extra event
    assert expected_item_count(count) == expected_draft_count(count) - rota_events
    assert expected_cross_file_duplicate_count() == rota_events


def test_rota_copy_shares_uids_but_drifts_bookkeeping() -> None:
    """The real export's four copies of one subscribed calendar share UIDs
    while CREATED/LAST-MODIFIED/DTSTAMP drift — the copy must be
    byte-different yet identity-identical."""
    members = calendar_members(6)
    rota_uids = _uids(members[ROTA_MEMBER])
    copy_uids = _uids(members[ROTA_COPY_MEMBER])
    assert set(rota_uids) < set(copy_uids)
    assert len(copy_uids) == len(rota_uids) + 1
    assert members[ROTA_MEMBER] != members[ROTA_COPY_MEMBER]


def test_fixed_anchor_events_present() -> None:
    """The DST pair, the all-day event, the bounded series + override, and
    the floating event are always generated — goldens pin their exact
    stored values."""
    life = calendar_members(4)[LIFE_MEMBER].decode()
    for anchor in (
        "DTSTART;TZID=America/New_York:20240309T120000",
        "DTSTART;TZID=America/New_York:20240311T120000",
        "DTSTART;VALUE=DATE:20240715",
        "RRULE:FREQ=WEEKLY;COUNT=8;BYDAY=TU",
        "RECURRENCE-ID;TZID=Asia/Kolkata:20240402T140000",
        "EXDATE;TZID=Asia/Kolkata:20240319T140000,20240326T140000",
        "DTSTART:20240501T090000",
        "BEGIN:VTIMEZONE",
        "BEGIN:VALARM",
        "STATUS:CANCELLED",
    ):
        assert anchor in life, anchor


def test_all_day_closed_form() -> None:
    count = 20
    members = calendar_members(count)
    date_starts = members[LIFE_MEMBER].count(b"DTSTART;VALUE=DATE:")
    assert date_starts == expected_all_day_count(count)


def test_synthetic_emails_only() -> None:
    """Attendee/organizer addresses live under @potluck.test — the PII guard
    must stay green on generated fixtures. (Unfold first: one attendee line
    deliberately folds inside its mailto address.)"""
    for name, member in calendar_members(12).items():
        text = member.decode().replace("\r\n ", "")  # RFC 5545 unfold
        for token in text.replace("\r\n", "\n").split():
            if "@" in token and "mailto:" in token:
                assert token.endswith("@potluck.test"), (name, token)


def test_write_export_dir_layout(tmp_path: Path) -> None:
    root = write_calendar_takeout(tmp_path, 6, seed=3, fmt="dir")
    assert root.name == "calendar-synth-001"
    assert (root / "Takeout" / "Calendar" / "Synthetic Life.ics").is_file()
    assert (root / "Takeout" / "Calendar" / "Synthetic Rota(1).ics").is_file()
    assert (root / "Takeout" / "Calendar" / "meet_settings.json").is_file()


def test_write_export_zip_naming(tmp_path: Path) -> None:
    archive = write_calendar_takeout(tmp_path, 6, seed=3)
    assert archive.name == "calendar-synth-001.zip"
