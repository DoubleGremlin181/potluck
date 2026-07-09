"""Tests for the Takeout Calendar source plugin.

Testing private helpers (_parse_calendar) is intentional: the recurrence
policy (one item per VEVENT master, overrides as their own items, NO
expansion), the ics:<uid>[:<recurrence-id-utc>] identity, and the timezone
discipline are the public contract of this module and must be covered at the
unit level, from synthetic bytes.

ICS shapes here mirror the real 2025-12 Takeout export (shape only — all
event content is synthetic).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from potluck.ingest.plugins import ParseContext, detect_sources, discover
from potluck.ingest.readers import open_archive
from potluck.ingest.sources.calendar import _parse_calendar, parse
from potluck.models.drafts import EventDraft
from potluck.models.items import ItemKind
from potluck.testing.archives import write_archive

_MEMBER = "Takeout/Calendar/Synthetic Calendar.ics"


def _vevent(*lines: str, uid: str | None = "ev-1@potluck.test") -> str:
    parts = ["BEGIN:VEVENT"]
    if uid is not None:
        parts.append(f"UID:{uid}")
    parts.extend(lines)
    parts.append("END:VEVENT")
    return "\r\n".join(parts) + "\r\n"


def _ics(*vevents: str, calname: str | None = "Synthetic Calendar") -> bytes:
    head = [
        "BEGIN:VCALENDAR",
        "PRODID:-//Synthetic//Potluck Tests//EN",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    if calname is not None:
        head.append(f"X-WR-CALNAME:{calname}")
        head.append("X-WR-TIMEZONE:UTC")
    return ("\r\n".join(head) + "\r\n" + "".join(vevents) + "END:VCALENDAR\r\n").encode()


def _drafts(data: bytes, member: str = _MEMBER) -> list[EventDraft]:
    return list(_parse_calendar(data, member))


# ---------------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------------


def test_basic_event_mapping() -> None:
    [d] = _drafts(
        _ics(
            _vevent(
                "DTSTART:20240605T140000Z",
                "DTEND:20240605T150000Z",
                "SUMMARY:Synthetic Standup",
                "DESCRIPTION:Agenda for the synthetic standup",
                "LOCATION:Harbor Room",
                "STATUS:CONFIRMED",
            )
        )
    )
    assert d.kind is ItemKind.EVENT
    assert d.external_id == "ics:ev-1@potluck.test"
    assert d.ts == datetime(2024, 6, 5, 14, 0, tzinfo=UTC)
    assert d.title == "Synthetic Standup"
    assert d.text == "Agenda for the synthetic standup\nHarbor Room"
    assert d.meta == {
        "calendar": "Synthetic Calendar",
        "status": "CONFIRMED",
        "end": "2024-06-05T15:00:00+00:00",
    }


def test_exporter_bookkeeping_is_never_stored() -> None:
    """DTSTAMP/CREATED/LAST-MODIFIED/SEQUENCE/TRANSP are export-time
    bookkeeping, not event content — the real Takeout duplicates the same
    subscribed calendar into several members whose copies differ ONLY in
    these fields, so storing them would defeat cross-member dedup."""
    [d] = _drafts(
        _ics(
            _vevent(
                "DTSTART:20240605T140000Z",
                "DTSTAMP:20251212T171747Z",
                "CREATED:20240101T000000Z",
                "LAST-MODIFIED:20251111T000000Z",
                "SEQUENCE:3",
                "TRANSP:OPAQUE",
                "SUMMARY:Synthetic Standup",
            )
        )
    )
    assert set(d.meta) == {"calendar"}


def test_title_and_text_absent_when_empty() -> None:
    [d] = _drafts(_ics(_vevent("DTSTART:20240605T140000Z", "SUMMARY:")))
    assert d.title is None
    assert d.text is None


def test_description_only_and_location_only_compose_text() -> None:
    [desc_only] = _drafts(_ics(_vevent("DTSTART:20240605T140000Z", "DESCRIPTION:Just the notes")))
    [loc_only] = _drafts(_ics(_vevent("DTSTART:20240605T140000Z", "LOCATION:Fjord Hall")))
    assert desc_only.text == "Just the notes"
    assert loc_only.text == "Fjord Hall"


def test_calendar_name_falls_back_to_member_stem() -> None:
    [d] = _drafts(
        _ics(_vevent("DTSTART:20240605T140000Z"), calname=None),
        member="Takeout/Calendar/Synthetic Rota(1).ics",
    )
    assert d.meta["calendar"] == "Synthetic Rota(1)"


def test_status_passes_through_verbatim() -> None:
    docs = _ics(
        _vevent("DTSTART:20240605T140000Z", "STATUS:CANCELLED", uid="a@potluck.test"),
        _vevent("DTSTART:20240606T140000Z", "STATUS:TENTATIVE", uid="b@potluck.test"),
        _vevent("DTSTART:20240607T140000Z", uid="c@potluck.test"),
    )
    cancelled, tentative, bare = _drafts(docs)
    assert cancelled.meta["status"] == "CANCELLED"
    assert tentative.meta["status"] == "TENTATIVE"
    assert "status" not in bare.meta


# ---------------------------------------------------------------------------
# Timezone discipline (acceptance criterion)
# ---------------------------------------------------------------------------


def test_tzid_datetime_converts_to_utc() -> None:
    """Asia/Kolkata (+05:30, no DST) — 14:00 local is 08:30Z."""
    [d] = _drafts(_ics(_vevent("DTSTART;TZID=Asia/Kolkata:20240305T140000")))
    assert d.ts == datetime(2024, 3, 5, 8, 30, tzinfo=UTC)


def test_dst_boundary_same_wall_clock_different_utc() -> None:
    """America/New_York springs forward on 2024-03-10: noon EST (-5) before
    the boundary is 17:00Z, noon EDT (-4) after it is 16:00Z. Same wall
    clock, different instants — THE timezone acceptance criterion."""
    before, after = _drafts(
        _ics(
            _vevent("DTSTART;TZID=America/New_York:20240309T120000", uid="a@potluck.test"),
            _vevent("DTSTART;TZID=America/New_York:20240311T120000", uid="b@potluck.test"),
        )
    )
    assert before.ts == datetime(2024, 3, 9, 17, 0, tzinfo=UTC)
    assert after.ts == datetime(2024, 3, 11, 16, 0, tzinfo=UTC)


def test_floating_datetime_treated_as_utc() -> None:
    """No TZID and no Z suffix: the same unknown-zone policy as whatsapp and
    gmail — assume UTC rather than guess a local zone."""
    [d] = _drafts(_ics(_vevent("DTSTART:20240501T090000")))
    assert d.ts == datetime(2024, 5, 1, 9, 0, tzinfo=UTC)


def test_unknown_tzid_falls_back_to_utc() -> None:
    """icalendar decodes an unresolvable TZID to a naive datetime; naive
    means UTC here (the floating-time policy covers it)."""
    [d] = _drafts(_ics(_vevent("DTSTART;TZID=Fake/Nowhere:20240501T090000")))
    assert d.ts == datetime(2024, 5, 1, 9, 0, tzinfo=UTC)


def test_dtend_converts_like_dtstart() -> None:
    [d] = _drafts(
        _ics(
            _vevent(
                "DTSTART;TZID=Asia/Kolkata:20240305T140000",
                "DTEND;TZID=Asia/Kolkata:20240305T150000",
            )
        )
    )
    assert d.meta["end"] == "2024-03-05T09:30:00+00:00"


def test_event_without_dtend_has_no_end_key() -> None:
    [d] = _drafts(_ics(_vevent("DTSTART:20240605T140000Z")))
    assert "end" not in d.meta


def test_all_day_event_utc_midnight_and_flag() -> None:
    """DATE-valued DTSTART → ts at UTC midnight + meta.all_day=true; the
    DATE-valued DTEND (exclusive next day) converts the same way."""
    [d] = _drafts(
        _ics(
            _vevent(
                "DTSTART;VALUE=DATE:20240715",
                "DTEND;VALUE=DATE:20240716",
                "SUMMARY:All day synthetic fair",
            )
        )
    )
    assert d.ts == datetime(2024, 7, 15, 0, 0, tzinfo=UTC)
    assert d.meta["all_day"] is True
    assert d.meta["end"] == "2024-07-16T00:00:00+00:00"


def test_timed_event_has_no_all_day_flag() -> None:
    [d] = _drafts(_ics(_vevent("DTSTART:20240605T140000Z")))
    assert "all_day" not in d.meta


def test_event_without_dtstart_is_kept_undated() -> None:
    """UID is the identity, DTSTART is content — a broken event without one
    still imports (undated) rather than vanishing."""
    [d] = _drafts(_ics(_vevent("SUMMARY:No start")))
    assert d.ts is None
    assert d.external_id == "ics:ev-1@potluck.test"


# ---------------------------------------------------------------------------
# Recurrence policy: one item per VEVENT, expansion deferred (P7)
# ---------------------------------------------------------------------------


def test_recurring_master_yields_one_item_with_rrule_meta() -> None:
    """A bounded 8-occurrence weekly series is ONE item — the rule rides
    meta verbatim; expansion into occurrences is deliberately deferred."""
    drafts = _drafts(
        _ics(
            _vevent(
                "DTSTART;TZID=Asia/Kolkata:20240305T140000",
                "RRULE:FREQ=WEEKLY;COUNT=8;BYDAY=TU",
                "SUMMARY:Weekly synthetic sync",
                uid="weekly-sync@potluck.test",
            )
        )
    )
    assert len(drafts) == 1
    assert drafts[0].external_id == "ics:weekly-sync@potluck.test"
    assert drafts[0].meta["rrule"] == "FREQ=WEEKLY;COUNT=8;BYDAY=TU"
    assert drafts[0].ts == datetime(2024, 3, 5, 8, 30, tzinfo=UTC)


def test_exdates_become_a_count() -> None:
    """EXDATE values are excluded instants of a rule that is itself not
    expanded — the count records that exclusions exist without minting
    occurrence data the timeline (P7) will re-derive."""
    [d] = _drafts(
        _ics(
            _vevent(
                "DTSTART;TZID=Asia/Kolkata:20240305T140000",
                "RRULE:FREQ=WEEKLY;COUNT=8;BYDAY=TU",
                "EXDATE;TZID=Asia/Kolkata:20240312T140000",
                "EXDATE;TZID=Asia/Kolkata:20240319T140000,20240326T140000",
            )
        )
    )
    assert d.meta["exdate_count"] == 3
    assert "rdate_count" not in d.meta


def test_rdates_become_a_count() -> None:
    [d] = _drafts(
        _ics(
            _vevent(
                "DTSTART:20240605T140000Z",
                "RRULE:FREQ=MONTHLY;COUNT=3",
                "RDATE:20240610T140000Z,20240611T140000Z",
            )
        )
    )
    assert d.meta["rdate_count"] == 2
    assert "exdate_count" not in d.meta


def test_non_recurring_event_has_no_recurrence_keys() -> None:
    [d] = _drafts(_ics(_vevent("DTSTART:20240605T140000Z")))
    for key in ("rrule", "exdate_count", "rdate_count", "recurrence_id"):
        assert key not in d.meta


def test_recurrence_override_is_its_own_item() -> None:
    """A RECURRENCE-ID VEVENT (a modified single occurrence) is a separate
    item whose identity appends the occurrence instant in UTC — 14:00
    Asia/Kolkata is 08:30Z."""
    master, override = _drafts(
        _ics(
            _vevent(
                "DTSTART;TZID=Asia/Kolkata:20240305T140000",
                "RRULE:FREQ=WEEKLY;COUNT=8;BYDAY=TU",
                "SUMMARY:Weekly synthetic sync",
                uid="weekly-sync@potluck.test",
            ),
            _vevent(
                "RECURRENCE-ID;TZID=Asia/Kolkata:20240402T140000",
                "DTSTART;TZID=Asia/Kolkata:20240402T150000",
                "SUMMARY:Weekly synthetic sync (moved)",
                uid="weekly-sync@potluck.test",
            ),
        )
    )
    assert master.external_id == "ics:weekly-sync@potluck.test"
    assert override.external_id == "ics:weekly-sync@potluck.test:20240402T083000Z"
    assert override.meta["recurrence_id"] == "20240402T083000Z"
    assert override.ts == datetime(2024, 4, 2, 9, 30, tzinfo=UTC)
    assert "rrule" not in override.meta
    assert "recurrence_id" not in master.meta


def test_date_valued_recurrence_id() -> None:
    """All-day series override: the identity suffix is the DATE itself."""
    [d] = _drafts(
        _ics(
            _vevent(
                "RECURRENCE-ID;VALUE=DATE:20240715",
                "DTSTART;VALUE=DATE:20240716",
                uid="daily@potluck.test",
            )
        )
    )
    assert d.external_id == "ics:daily@potluck.test:20240715"
    assert d.meta["recurrence_id"] == "20240715"


def test_utc_recurrence_id_normalizes_identically() -> None:
    """The same instant spelled as UTC or as a TZID local time must mint the
    SAME identity — Takeout re-exports may switch spellings."""
    [utc_form] = _drafts(
        _ics(
            _vevent(
                "RECURRENCE-ID:20240402T083000Z",
                "DTSTART:20240402T093000Z",
                uid="weekly-sync@potluck.test",
            )
        )
    )
    assert utc_form.external_id == "ics:weekly-sync@potluck.test:20240402T083000Z"


def test_multiple_rrules_are_joined() -> None:
    """RFC 5545 allows at most one RRULE but real feeds have shipped more;
    both survive, newline-joined, rather than one silently winning."""
    [d] = _drafts(
        _ics(
            _vevent(
                "DTSTART:20240605T140000Z",
                "RRULE:FREQ=WEEKLY;COUNT=4",
                "RRULE:FREQ=MONTHLY;COUNT=2",
            )
        )
    )
    assert d.meta["rrule"] == "FREQ=WEEKLY;COUNT=4\nFREQ=MONTHLY;COUNT=2"


# ---------------------------------------------------------------------------
# Text fidelity: folding + escaping (icalendar owns the decoding)
# ---------------------------------------------------------------------------


def test_folded_lines_unfold() -> None:
    """A 75-octet-folded SUMMARY (continuation lines start with one space)
    reassembles without the fold artifacts."""
    data = _ics(
        "BEGIN:VEVENT\r\n"
        "UID:folded@potluck.test\r\n"
        "DTSTART:20240605T140000Z\r\n"
        "SUMMARY:A deliberately long synthetic summary that exceeds the sevent\r\n"
        " y-five octet line limit and therefore folds across physical lines\r\n"
        "END:VEVENT\r\n"
    )
    [d] = _drafts(data)
    assert d.title == (
        "A deliberately long synthetic summary that exceeds the seventy-five "
        "octet line limit and therefore folds across physical lines"
    )


def test_escaped_text_unescapes() -> None:
    r"""\, \; \n and \\ in SUMMARY/DESCRIPTION decode to the literal chars."""
    [d] = _drafts(
        _ics(
            _vevent(
                "DTSTART:20240605T140000Z",
                "SUMMARY:Dinner\\, drinks\\; dessert",
                "DESCRIPTION:Line one\\nLine two\\, with comma and back\\\\slash",
            )
        )
    )
    assert d.title == "Dinner, drinks; dessert"
    assert d.text == "Line one\nLine two, with comma and back\\slash"


# ---------------------------------------------------------------------------
# PII posture: attendees/organizer are counts, never addresses
# ---------------------------------------------------------------------------


def test_attendees_become_a_count_never_addresses() -> None:
    [d] = _drafts(
        _ics(
            _vevent(
                "DTSTART:20240605T140000Z",
                "ORGANIZER;CN=Synthetic Organizer:mailto:organizer@potluck.test",
                "ATTENDEE;CN=Attendee One;PARTSTAT=ACCEPTED:mailto:attendee-1@potluck.test",
                "ATTENDEE;CN=Attendee Two;PARTSTAT=DECLINED:mailto:attendee-2@potluck.test",
                "ATTENDEE;CN=Attendee Three;PARTSTAT=NEEDS-ACTION:mailto:attendee-3@potluck.test",
                "SUMMARY:Synthetic planning",
            )
        )
    )
    assert d.meta["attendee_count"] == 3
    assert d.meta["has_organizer"] is True
    stored = f"{d.title} {d.text} {d.meta!r}"
    assert "attendee-1" not in stored
    assert "organizer@" not in stored
    assert "mailto" not in stored


def test_single_attendee_counts_as_one() -> None:
    [d] = _drafts(
        _ics(
            _vevent(
                "DTSTART:20240605T140000Z",
                "ATTENDEE;PARTSTAT=ACCEPTED:mailto:attendee-1@potluck.test",
            )
        )
    )
    assert d.meta["attendee_count"] == 1
    assert "has_organizer" not in d.meta


def test_event_without_attendees_has_no_count_keys() -> None:
    [d] = _drafts(_ics(_vevent("DTSTART:20240605T140000Z")))
    assert "attendee_count" not in d.meta
    assert "has_organizer" not in d.meta


def test_valarm_content_never_leaks_into_the_event() -> None:
    """VALARM sub-components carry their own DESCRIPTION (a reminder blurb,
    165 of them in the real export) — never the event's text."""
    [d] = _drafts(
        _ics(
            _vevent(
                "DTSTART:20240605T140000Z",
                "SUMMARY:Event with alarm",
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "DESCRIPTION:alarm blurb decoy",
                "TRIGGER:-PT15M",
                "END:VALARM",
            )
        )
    )
    assert d.title == "Event with alarm"
    assert d.text is None


# ---------------------------------------------------------------------------
# Identity across members + containment
# ---------------------------------------------------------------------------


def test_same_uid_across_members_shares_identity(tmp_path: Path) -> None:
    """Takeout duplicates one subscribed calendar into several .ics members
    (the real export carries four copies of one calendar, 386 shared UIDs):
    both copies mint the SAME external_id, so the engine dedups them rather
    than double-importing — no per-calendar namespacing."""
    event = _vevent("DTSTART:20240605T140000Z", "SUMMARY:Shared fixture", uid="s@potluck.test")
    members = {
        "Takeout/Calendar/Synthetic Rota.ics": _ics(event, calname="Synthetic Rota"),
        "Takeout/Calendar/Synthetic Rota(1).ics": _ics(event, calname="Synthetic Rota"),
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    drafts = list(parse(open_archive(archive), ParseContext()))
    assert len(drafts) == 2
    assert len({d.external_id for d in drafts}) == 1


def test_event_without_uid_is_skipped_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(
            _ics(
                _vevent("DTSTART:20240605T140000Z", uid=None),
                _vevent("DTSTART:20240606T140000Z", uid="ok@potluck.test"),
            )
        )
    assert len(drafts) == 1
    assert drafts[0].external_id == "ics:ok@potluck.test"
    assert any("UID" in r.message for r in caplog.records)


def test_unparseable_member_warns_and_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(b"not an ics file at all")
    assert drafts == []
    assert any("skipped" in r.message for r in caplog.records)


def test_empty_calendar_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    """A calendar with zero events is a legitimate export, not a failure."""
    with caplog.at_level(logging.WARNING):
        assert _drafts(_ics()) == []
    assert not caplog.records


def test_undecodable_dtstart_keeps_event_undated(caplog: pytest.LogCaptureFixture) -> None:
    """icalendar tolerates a garbage DTSTART (the real export ships events
    libical already flagged with X-LIC-ERROR) — the event imports undated."""
    with caplog.at_level(logging.WARNING):
        [d] = _drafts(_ics(_vevent("DTSTART:garbage", "SUMMARY:Broken start")))
    assert d.ts is None
    assert d.title == "Broken start"


# ---------------------------------------------------------------------------
# Detection + parse() over archives
# ---------------------------------------------------------------------------


def test_detection_matches_export_layout_precisely(tmp_path: Path) -> None:
    matches = {
        # The real 2025-12 layout: Takeout/Calendar/<calendar name>.ics with
        # (N)-suffixed duplicate members, plus root-relative and re-zipped
        # deeper variants.
        "Takeout/Calendar/Synthetic Name.ics": True,
        "Takeout/Calendar/Synthetic Name(1).ics": True,
        "Calendar/Synthetic Name.ics": True,
        "wrapper/Takeout/Calendar/Synthetic Name.ics": True,
        # The Calendar folder's sibling member and neighbours must never match.
        "Takeout/Calendar/meet_settings.json": False,
        "Takeout/My Activity/Calendar/MyActivity.html": False,
        # Generic names NEVER detect — a lone .ics without its Calendar/
        # folder is the generic ingesters' (#150) territory.
        "event.ics": False,
        "invite.ics": False,
        "NotCalendar/Synthetic Name.ics": False,
        "Takeout/Calendar/Synthetic Name.ics.bak": False,
        "takeout/calendar/synthetic name.ics": False,  # matching is case-sensitive
        "Takeout/Calendar/Synthetic Name.ICS": False,
    }
    plugin = discover()["calendar"]
    for name, expected in matches.items():
        assert plugin.detect.matches(name) is expected, name

    members = {"Takeout/Calendar/Synthetic Name.ics": _ics(_vevent("DTSTART:20240605T140000Z"))}
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    assert [p.name for p in detect_sources(open_archive(archive))] == ["calendar"]


def test_parse_reads_calendars_and_skips_sibling_members(tmp_path: Path) -> None:
    members = {
        "Takeout/Calendar/Synthetic Calendar.ics": _ics(
            _vevent("DTSTART:20240605T140000Z", "SUMMARY:Kept")
        ),
        "Takeout/Calendar/meet_settings.json": b'{"Meeting data": []}',
        "Takeout/My Activity/Calendar/MyActivity.html": b"<html>decoy</html>",
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    drafts = list(parse(open_archive(archive), ParseContext()))
    assert len(drafts) == 1
    assert isinstance(drafts[0], EventDraft)
    assert drafts[0].title == "Kept"


def test_parse_handles_nested_layout(tmp_path: Path) -> None:
    members = {
        "wrapper/Takeout/Calendar/Synthetic Calendar.ics": _ics(_vevent("DTSTART:20240605T140000Z"))
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    assert [p.name for p in detect_sources(open_archive(archive))] == ["calendar"]
    assert len(list(parse(open_archive(archive), ParseContext()))) == 1


def test_parse_empty_archive_yields_nothing(tmp_path: Path) -> None:
    archive = write_archive(tmp_path / "empty.zip", {"decoy/readme.md": b"x"}, "zip")
    assert list(parse(open_archive(archive), ParseContext())) == []
