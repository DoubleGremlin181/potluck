"""Deterministic synthetic Calendar-Takeout generator.

Ships inside ``potluck.testing`` so tests, committed fixtures, and bench
scenarios share one deterministic source. Same arguments → identical bytes on
every machine, forever. Never put real personal data here — every address
lives under ``@potluck.test``, summaries come from the shared WORDS list.

The member set mirrors the real 2025-12 export: one ``.ics`` per calendar
under ``Takeout/Calendar/``, a duplicated subscription under Takeout's
``(N)``-suffix naming (the real export carries FOUR copies of one calendar
whose events share UIDs while CREATED/LAST-MODIFIED/DTSTAMP drift), and the
``meet_settings.json`` sibling the parser must never read.

``Synthetic Life.ics`` holds ``count`` indexed events (DTSTART shape rotates
by ``i % 4``: UTC ``Z`` / Asia/Kolkata / America/Los_Angeles without DTEND /
all-day DATE) plus fixed anchor events with closed-form outcomes the goldens
pin exactly:

- the DST pair: noon America/New_York on both sides of the 2024-03-10
  spring-forward (17:00Z before, 16:00Z after)
- an all-day event (DATE-valued DTSTART/DTEND)
- a bounded RRULE series (COUNT=8) with 3 EXDATEs across 2 lines, plus its
  RECURRENCE-ID override (moved, then CANCELLED)
- a folded (>75 octets) SUMMARY with escaped ``\\,`` ``\\;`` and a
  DESCRIPTION with escaped ``\\n``
- a floating (no TZID) event and an attendees event (ORGANIZER + 3
  ATTENDEEs, one folded mid-address) with a VALARM decoy

``Synthetic Rota.ics`` and its ``(1)`` copy share 4 UIDs with byte-different
bookkeeping; the copy carries 1 extra event — so one import yields
:func:`expected_item_count` new items and
:func:`expected_cross_file_duplicate_count` duplicates.

Regenerate the golden fixture::

    python -c "
    from pathlib import Path
    from potluck.testing.calendar import write_calendar_takeout
    write_calendar_takeout(Path('tests/fixtures/calendar'), 20, seed=11, fmt='dir')
    "
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Final, Literal

from potluck.testing.archives import write_archive
from potluck.testing.generators import WORDS

LIFE_MEMBER: Final = "Takeout/Calendar/Synthetic Life.ics"
ROTA_MEMBER: Final = "Takeout/Calendar/Synthetic Rota.ics"
ROTA_COPY_MEMBER: Final = "Takeout/Calendar/Synthetic Rota(1).ics"
_SETTINGS_MEMBER: Final = "Takeout/Calendar/meet_settings.json"

_FIXED_EVENTS: Final = 8  # anchor events in Synthetic Life (7 UIDs + 1 override)
_ROTA_SHARED: Final = 4  # events present in BOTH Rota members
_ROTA_EXTRA: Final = 1  # only in the (1) copy

# meet_settings.json shape mirrors the real sibling member (decoy only).
_SETTINGS_BYTES: Final = (
    b'{"Meeting data": [\n  {\n    "Meeting code": "syn-thet-icc",\n'
    b'    "Live stream access config": {\n'
    b'      "Live stream access type": "ACCESS_TYPE_UNSPECIFIED",\n'
    b'      "Allowlist": []\n    }\n  }\n]}\n'
)

_MAX_OCTETS: Final = 75  # RFC 5545 physical-line limit


def expected_item_count(count: int) -> int:
    """Items one import of the generated Takeout yields: the indexed and
    anchor events plus the rota events counted ONCE (shared UIDs dedup)."""
    return count + _FIXED_EVENTS + _ROTA_SHARED + _ROTA_EXTRA


def expected_draft_count(count: int) -> int:
    """VEVENTs across all members (the shared rota events appear twice)."""
    return count + _FIXED_EVENTS + 2 * _ROTA_SHARED + _ROTA_EXTRA


def expected_cross_file_duplicate_count() -> int:
    """Drafts that dedup against another member's copy of the same UID."""
    return _ROTA_SHARED


def expected_all_day_count(count: int) -> int:
    """DATE-valued events: the fixed all-day anchor plus indexed ``i%4==3``."""
    return 1 + sum(1 for i in range(count) if i % 4 == 3)


def _escape(value: str) -> str:
    r"""RFC 5545 TEXT escaping: ``\`` ``;`` ``,`` and newline."""
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> list[str]:
    """RFC 5545 folding: physical lines of at most 75 octets; continuation
    lines start with one space (counted inside their 75)."""
    physical: list[str] = []
    current = ""
    for char in line:
        if len(current.encode()) + len(char.encode()) > _MAX_OCTETS:
            physical.append(current)
            current = " "
        current += char
    physical.append(current)
    return physical


def _block(name: str, lines: list[str]) -> str:
    """One BEGIN/END component with every content line folded."""
    folded: list[str] = []
    for line in [f"BEGIN:{name}", *lines, f"END:{name}"]:
        folded.extend(_fold(line))
    return "\r\n".join(folded) + "\r\n"


def _words(salt: int, i: int, offset: int, k: int) -> str:
    return " ".join(WORDS[(salt + i * 7 + (offset + j) * 3) % len(WORDS)] for j in range(k))


def _bookkeeping(stamp: str) -> list[str]:
    """Exporter bookkeeping the parser must DROP (the real export's
    duplicate calendar copies differ only in these)."""
    return [f"DTSTAMP:{stamp}", "CREATED:20240101T000000Z", f"LAST-MODIFIED:{stamp}"]


# America/New_York with the post-2007 US rules, as the real export writes it.
_VTIMEZONE: Final = (
    "BEGIN:VTIMEZONE\r\nTZID:America/New_York\r\nX-LIC-LOCATION:America/New_York\r\n"
    "BEGIN:DAYLIGHT\r\nTZOFFSETFROM:-0500\r\nTZOFFSETTO:-0400\r\nTZNAME:EDT\r\n"
    "DTSTART:19700308T020000\r\nRRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\r\nEND:DAYLIGHT\r\n"
    "BEGIN:STANDARD\r\nTZOFFSETFROM:-0400\r\nTZOFFSETTO:-0500\r\nTZNAME:EST\r\n"
    "DTSTART:19701101T020000\r\nRRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\r\nEND:STANDARD\r\n"
    "END:VTIMEZONE\r\n"
)


def _fixed_events() -> list[str]:
    """The anchor events — goldens pin their exact stored values."""
    folded_summary = _escape(
        "Quarterly planning, review; and a deliberately overlong synthetic summary that folds"
    )
    escaped_description = _escape("Agenda line one\nAgenda line two, with an escaped comma")
    return [
        _block(
            "VEVENT",
            [
                "DTSTART;TZID=America/New_York:20240309T120000",
                "DTEND;TZID=America/New_York:20240309T130000",
                *_bookkeeping("20251212T171747Z"),
                "UID:dst-before@potluck.test",
                "SEQUENCE:0",
                "STATUS:CONFIRMED",
                "SUMMARY:Winter planning huddle",
                "LOCATION:Harbor Room",
                "TRANSP:OPAQUE",
            ],
        ),
        _block(
            "VEVENT",
            [
                "DTSTART;TZID=America/New_York:20240311T120000",
                "DTEND;TZID=America/New_York:20240311T130000",
                *_bookkeeping("20251212T171747Z"),
                "UID:dst-after@potluck.test",
                "SEQUENCE:0",
                "STATUS:CONFIRMED",
                "SUMMARY:Spring planning huddle",
                "LOCATION:Harbor Room",
                "TRANSP:OPAQUE",
            ],
        ),
        _block(
            "VEVENT",
            [
                "DTSTART;VALUE=DATE:20240715",
                "DTEND;VALUE=DATE:20240716",
                *_bookkeeping("20251212T171747Z"),
                "UID:all-day-fair@potluck.test",
                "SEQUENCE:0",
                "STATUS:CONFIRMED",
                "SUMMARY:All day synthetic fair",
                "TRANSP:TRANSPARENT",
            ],
        ),
        _block(
            "VEVENT",
            [
                "DTSTART;TZID=Asia/Kolkata:20240305T140000",
                "DTEND;TZID=Asia/Kolkata:20240305T150000",
                "RRULE:FREQ=WEEKLY;COUNT=8;BYDAY=TU",
                "EXDATE;TZID=Asia/Kolkata:20240312T140000",
                "EXDATE;TZID=Asia/Kolkata:20240319T140000,20240326T140000",
                *_bookkeeping("20251212T171747Z"),
                "UID:weekly-sync@potluck.test",
                "SEQUENCE:0",
                "STATUS:CONFIRMED",
                "SUMMARY:Weekly synthetic sync",
                "DESCRIPTION:Recurring agenda",
                "TRANSP:OPAQUE",
            ],
        ),
        _block(
            "VEVENT",
            [
                "RECURRENCE-ID;TZID=Asia/Kolkata:20240402T140000",
                "DTSTART;TZID=Asia/Kolkata:20240402T150000",
                "DTEND;TZID=Asia/Kolkata:20240402T160000",
                *_bookkeeping("20251212T171747Z"),
                "UID:weekly-sync@potluck.test",
                "SEQUENCE:1",
                "STATUS:CANCELLED",
                "SUMMARY:Weekly synthetic sync (moved)",
                "TRANSP:OPAQUE",
            ],
        ),
        _block(
            "VEVENT",
            [
                "DTSTART:20240620T170000Z",
                "DTEND:20240620T180000Z",
                *_bookkeeping("20251212T171747Z"),
                "UID:folded-escaped@potluck.test",
                "SEQUENCE:0",
                "STATUS:CONFIRMED",
                f"SUMMARY:{folded_summary}",
                f"DESCRIPTION:{escaped_description}",
                f"LOCATION:{_escape('Harbor House, Suite 7')}",
                "TRANSP:OPAQUE",
            ],
        ),
        _block(
            "VEVENT",
            [
                "DTSTART:20240501T090000",
                *_bookkeeping("20251212T171747Z"),
                "UID:floating@potluck.test",
                "SEQUENCE:0",
                "STATUS:CONFIRMED",
                "SUMMARY:Floating focus block",
                "TRANSP:OPAQUE",
            ],
        ),
        _block(
            "VEVENT",
            [
                "DTSTART:20240605T140000Z",
                "DTEND:20240605T150000Z",
                *_bookkeeping("20251212T171747Z"),
                "UID:team-plan@potluck.test",
                "ORGANIZER;CN=Synthetic Organizer:mailto:organizer@potluck.test",
                "ATTENDEE;CN=Attendee One;PARTSTAT=ACCEPTED:mailto:attendee-1@potluck.test",
                "ATTENDEE;CN=Attendee Two;PARTSTAT=DECLINED:mailto:attendee-2@potluck.test",
                # 80 octets — exercises a fold INSIDE a mailto address.
                "ATTENDEE;CN=Attendee Three;PARTSTAT=NEEDS-ACTION:mailto:attendee-3@potluck.test",
                "SEQUENCE:0",
                "STATUS:TENTATIVE",
                "SUMMARY:Synthetic team planning",
                "TRANSP:OPAQUE",
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "DESCRIPTION:alarm blurb decoy",
                "TRIGGER:-PT15M",
                "END:VALARM",
            ],
        ),
    ]


def _indexed_event(i: int, salt: int) -> str:
    """One filler event; the DTSTART shape rotates by ``i % 4``."""
    day = date(2024, 1, 1) + timedelta(days=i)
    stamp = day.strftime("%Y%m%d")
    if i % 4 == 0:
        starts = [f"DTSTART:{stamp}T091500Z", f"DTEND:{stamp}T101500Z"]
    elif i % 4 == 1:
        starts = [
            f"DTSTART;TZID=Asia/Kolkata:{stamp}T184500",
            f"DTEND;TZID=Asia/Kolkata:{stamp}T194500",
        ]
    elif i % 4 == 2:
        # No DTEND — ~3% of the real export's events are point events.
        starts = [f"DTSTART;TZID=America/Los_Angeles:{stamp}T080500"]
    else:
        next_stamp = (day + timedelta(days=1)).strftime("%Y%m%d")
        starts = [f"DTSTART;VALUE=DATE:{stamp}", f"DTEND;VALUE=DATE:{next_stamp}"]

    lines = [
        *starts,
        *_bookkeeping(f"2025120{1 + i % 9}T0{i % 6}0000Z"),
        f"UID:synth-{i}@potluck.test",
        "SEQUENCE:0",
        "STATUS:" + ("TENTATIVE" if i % 7 == 5 else "CONFIRMED"),
        f"SUMMARY:{_escape(_words(salt, i, 0, 3 + i % 3))}",
    ]
    if i % 3 != 1:
        lines.append(f"DESCRIPTION:{_escape(_words(salt, i, 20, 6))}")
    if i % 5 == 2:
        lines.append(f"LOCATION:{_escape(_words(salt, i, 40, 1).capitalize())} Hall {i}")
    lines.append("TRANSP:OPAQUE")
    return _block("VEVENT", lines)


def _rota_event(j: int, salt: int, stamp: str) -> str:
    """One shared-rota event; *stamp* is the bookkeeping drift between the
    two member copies (never stored, so the copies still dedup)."""
    day = date(2024, 9, 2) + timedelta(days=j)
    return _block(
        "VEVENT",
        [
            f"DTSTART:{day.strftime('%Y%m%d')}T100000Z",
            f"DTEND:{day.strftime('%Y%m%d')}T110000Z",
            *_bookkeeping(stamp),
            f"UID:rota-{j}@potluck.test",
            "SEQUENCE:0",
            "STATUS:CONFIRMED",
            f"SUMMARY:{_escape(_words(salt, j, 60, 3))}",
            "TRANSP:OPAQUE",
        ],
    )


def _calendar_bytes(calname: str, blocks: list[str]) -> bytes:
    head = [
        "BEGIN:VCALENDAR",
        "PRODID:-//Synthetic//Potluck Generator//EN",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{calname}",
        "X-WR-TIMEZONE:UTC",
    ]
    return ("\r\n".join(head) + "\r\n" + "".join(blocks) + "END:VCALENDAR\r\n").encode()


def calendar_members(count: int, seed: int = 42) -> dict[str, bytes]:
    """The member set of one synthetic Takeout ({posix_name: content})."""
    salt = seed * 1009
    life = _calendar_bytes(
        "Synthetic Life",
        [_VTIMEZONE, *_fixed_events(), *(_indexed_event(i, salt) for i in range(count))],
    )
    shared = [_rota_event(j, salt, "20251212T171747Z") for j in range(_ROTA_SHARED)]
    drifted = [_rota_event(j, salt, "20251213T093000Z") for j in range(_ROTA_SHARED)]
    extra = _rota_event(_ROTA_SHARED + 6, salt, "20251213T093000Z")
    return {
        LIFE_MEMBER: life,
        ROTA_MEMBER: _calendar_bytes("Synthetic Rota", shared),
        ROTA_COPY_MEMBER: _calendar_bytes("Synthetic Rota", [*drifted, extra]),
        _SETTINGS_MEMBER: _SETTINGS_BYTES,
    }


def write_calendar_takeout(
    dest_dir: Path,
    count: int,
    seed: int = 42,
    *,
    fmt: Literal["zip", "tgz", "dir"] = "zip",
) -> Path:
    """Materialise a synthetic Calendar Takeout archive in *dest_dir*.

    Returns the archive path (or the directory root for ``fmt="dir"``).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    members = calendar_members(count, seed)
    if fmt == "dir":
        dest = dest_dir / "calendar-synth-001"
        write_archive(dest, members, "dir")
        return dest
    ext = "zip" if fmt == "zip" else "tgz"
    dest = dest_dir / f"calendar-synth-001.{ext}"
    write_archive(dest, members, fmt)
    return dest
