"""Takeout Calendar source plugin.

Parses the .ics members inside Google Takeout's Calendar folder via the
``icalendar`` package (RFC 5545 unfolding/unescaping/type decoding — never
hand-rolled). Format spec (v1 authoritative; verified against a real 2025-12
Takeout, shape only):

- Members: ``Takeout/Calendar/<calendar name>.ics``, one per calendar, with
  Takeout's ``(N)``-suffix naming when it exports several copies of the same
  calendar (the real export carries FOUR copies of one subscribed calendar).
  The sibling ``meet_settings.json`` is conferencing state, not personal
  records — never matched.
- Every VEVENT of the real export carries UID/DTSTART/STATUS; DTEND is
  missing on ~3% (point events); DTSTART values appear UTC-``Z`` (95%),
  all-day ``VALUE=DATE`` (4%), and ``TZID``-local (IANA names); VALARM
  sub-components and libical ``X-LIC-ERROR`` annotations appear and are
  never event content.

Recurrence expansion POLICY (the #146 deliverable): a recurring series is
ONE item — the VEVENT master with ``ts`` = DTSTART and the RRULE riding
``meta.rrule`` verbatim; EXDATE/RDATE survive as ``meta.exdate_count`` /
``meta.rdate_count``. Modified single occurrences (VEVENTs with a
RECURRENCE-ID) are their OWN items — their identity appends the occurrence
instant. Expansion into individual occurrence rows is deliberately DEFERRED:
unbounded rules never materialize, and the P7 timeline UI is where
occurrences matter — it can expand ``meta.rrule`` on read without any
schema change.

Kind mapping: calendar events → ``kind=event``; VTODO/VJOURNAL components
are deliberately ignored (not calendar records; Google exports tasks and
notes through other products). No satellite: ``title`` is SUMMARY; ``text``
is DESCRIPTION + LOCATION (newline-joined, both FTS-searchable); ``ts`` is
DTSTART converted to UTC. meta carries the calendar display name
(X-WR-CALNAME, falling back to the member stem), STATUS verbatim, the end
instant (``meta.end``, ISO UTC — stored with the export's RFC 5545
EXCLUSIVE-end semantics, so an all-day event's ``meta.end`` is the NEXT
day's midnight; P7 display must render ranges accordingly),
``meta.all_day`` on DATE-valued events, and the recurrence fields above.
Exporter bookkeeping (DTSTAMP/CREATED/LAST-MODIFIED/SEQUENCE/TRANSP) is
dropped: the real export's duplicate calendar copies differ ONLY in those
fields, so storing them would defeat cross-member dedup.

PII posture: ATTENDEE/ORGANIZER values are mailto: addresses of OTHER
people — stored as ``meta.attendee_count`` + ``meta.has_organizer`` only,
never addresses or CNs. The user's own participation status (PARTSTAT on
their own attendee line) is NOT derivable without knowing the user's
address, which Potluck deliberately does not configure — omitted rather
than guessed.

Identity policy: ``ics:<uid>`` (+ ``:<recurrence-id-utc>`` for overrides,
compact UTC form — the same instant spelled with a TZID or as UTC mints the
same id). iCalendar UIDs are native identities, so no occurrence counters.
The SAME UID appears across calendar members in one export: the real
export's four copies of one subscription share 386 of 387 UIDs per copy
(387 of 1714 keys overall), and (UID, RECURRENCE-ID) is unique WITHIN every
member. Copies are therefore deduped globally, not namespaced per calendar
— per-member namespacing would mint ~1160 phantom duplicates. Copies whose
content drifted (the exporter refreshed a DESCRIPTION between copies)
reconcile through the engine's identity path as updates, adjudicated
deterministically: the LAST member in archive order wins. Accepted
residual: two genuinely DIFFERENT events from unrelated third-party feeds
that reuse one UID would silently merge under that same last-wins rule.
RFC 5545 declares UIDs globally unique and Google's exporter honors that,
so the collision cost (one visible update) is taken over namespacing's
guaranteed ~1160 phantoms.

Timezone discipline: TZID-aware and UTC datetimes convert to UTC exactly
(zoneinfo owns DST); floating datetimes (no TZID) are treated AS UTC — the
same unknown-zone policy as whatsapp/gmail — and an unresolvable TZID
decodes naive and lands in the same bucket; all-day DATE values become UTC
midnight + ``meta.all_day=true``.

Containment: a member icalendar cannot parse logs one WARNING and is
skipped (other members still import); a VEVENT without a UID logs one
WARNING and is skipped (identity needs it); a missing or undecodable
DTSTART logs one WARNING and keeps the event, undated. An empty calendar
is a legitimate export and stays silent.

Detection is anchored on the ``Calendar/`` parent segment: bare ``*.ics``
(a generic extension, the #150 generic ingesters' territory) never matches.
Consequence: a hand-extracted lone .ics without its Calendar/ folder is
deliberately not detected.
"""

import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Final

from icalendar import BrokenCalendarProperty, Calendar, Component, vRecur
from pydantic import JsonValue

from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import EventDraft
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)

# '*/' (with '*' crossing '/') covers the standard Takeout/ nesting and
# re-zipped deeper layouts, the bare alternative a root-relative Calendar/
# folder. meet_settings.json and My Activity/Calendar/*.html can never match.
_EXPORT_GLOB = Glob("Calendar/*.ics|*/Calendar/*.ics")

_UTC_COMPACT: Final = "%Y%m%dT%H%M%SZ"
_DATE_COMPACT: Final = "%Y%m%d"


def _first(prop: object) -> object:
    """Collapse icalendar's list-when-repeated property shape to one value
    (a property RFC 5545 allows once can still legally repeat in the wild)."""
    if isinstance(prop, list):
        return prop[0] if prop else None
    return prop


def _text_or_none(prop: object) -> str | None:
    """The str value of a text property; None when absent or blank."""
    if prop is None:
        return None
    value = str(prop)
    return value if value.strip() else None


def _dt_value(prop: object) -> date | datetime | None:
    """The decoded date/datetime behind a DTSTART/DTEND/RECURRENCE-ID
    property; None when absent or undecodable (icalendar 7 wraps a garbage
    value in a broken-property object whose ``.dt`` raises — the real
    export ships events libical already flagged with X-LIC-ERROR)."""
    try:
        value = getattr(prop, "dt", None)
    except BrokenCalendarProperty:
        return None
    return value if isinstance(value, date | datetime) else None


def _to_utc(value: date | datetime) -> datetime:
    """UTC instant of an iCalendar date/datetime value.

    Aware datetimes convert exactly (zoneinfo owns DST); naive datetimes
    (floating or unresolvable TZID) are treated AS UTC per the module
    policy; DATE values (all-day) become UTC midnight.
    """
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _compact_utc(value: date | datetime) -> str:
    """Compact UTC identity suffix for a RECURRENCE-ID: ``YYYYMMDDTHHMMSSZ``
    for datetimes (TZID and UTC spellings of one instant collapse to the
    same string), ``YYYYMMDD`` for all-day DATE values."""
    if isinstance(value, datetime):
        return _to_utc(value).strftime(_UTC_COMPACT)
    return value.strftime(_DATE_COMPACT)


def _dt_list_count(prop: object) -> int:
    """Total instants across EXDATE/RDATE property lines (each line is a
    vDDDLists carrying one or more values); 0 when absent."""
    if prop is None:
        return 0
    lines = prop if isinstance(prop, list) else [prop]
    return sum(len(getattr(line, "dts", ())) for line in lines)


def _rrule_text(prop: object) -> str | None:
    """The rule(s) re-serialized by icalendar (content-identical to the
    export); multiple RRULE lines survive newline-joined rather than one
    silently winning. None when absent or nothing decoded to a rule."""
    if prop is None:
        return None
    rules = prop if isinstance(prop, list) else [prop]
    parts: list[str] = []
    for rule in rules:
        if isinstance(rule, vRecur):
            raw: bytes = rule.to_ical()  # type: ignore[no-untyped-call]  # untyped upstream
            parts.append(raw.decode("utf-8"))
    return "\n".join(parts) if parts else None


def _build_draft(event: Component, uid: str, calendar_name: str) -> EventDraft:
    """Assemble one VEVENT component into a draft; *uid* already verified."""
    external_id = f"ics:{uid}"

    meta: dict[str, JsonValue] = {"calendar": calendar_name}

    status = _text_or_none(_first(event.get("STATUS")))
    if status is not None:
        meta["status"] = status

    dtstart = _dt_value(_first(event.get("DTSTART")))
    ts = _to_utc(dtstart) if dtstart is not None else None
    if dtstart is not None and not isinstance(dtstart, datetime):
        meta["all_day"] = True

    dtend = _dt_value(_first(event.get("DTEND")))
    if dtend is not None:
        meta["end"] = _to_utc(dtend).isoformat()

    rrule = _rrule_text(event.get("RRULE"))
    if rrule is not None:
        meta["rrule"] = rrule
    exdate_count = _dt_list_count(event.get("EXDATE"))
    if exdate_count:
        meta["exdate_count"] = exdate_count
    rdate_count = _dt_list_count(event.get("RDATE"))
    if rdate_count:
        meta["rdate_count"] = rdate_count

    recurrence_id = _dt_value(_first(event.get("RECURRENCE-ID")))
    if recurrence_id is not None:
        suffix = _compact_utc(recurrence_id)
        external_id = f"{external_id}:{suffix}"
        meta["recurrence_id"] = suffix

    attendees = event.get("ATTENDEE")
    if attendees is not None:
        meta["attendee_count"] = len(attendees) if isinstance(attendees, list) else 1
    if event.get("ORGANIZER") is not None:
        meta["has_organizer"] = True

    description = _text_or_none(_first(event.get("DESCRIPTION")))
    location = _text_or_none(_first(event.get("LOCATION")))
    text = "\n".join(part for part in (description, location) if part is not None) or None

    return EventDraft(
        external_id=external_id,
        ts=ts,
        title=_text_or_none(_first(event.get("SUMMARY"))),
        text=text,
        meta=meta,
    )


def _parse_calendar(data: bytes, member_name: str) -> Iterator[EventDraft]:
    """Yield EventDrafts from one .ics member.

    icalendar owns unfolding/unescaping/type decoding; a member it cannot
    parse logs one WARNING and is skipped — other members still import.
    """
    try:
        calendar = Calendar.from_ical(data)
    except ValueError as exc:
        _logger.warning("calendar: cannot parse %r: %s — member skipped", member_name, exc)
        return

    stem = member_name.rsplit("/", 1)[-1].removesuffix(".ics")
    calendar_name = _text_or_none(_first(calendar.get("X-WR-CALNAME"))) or stem

    for ordinal, event in enumerate(calendar.walk("VEVENT"), start=1):
        uid = _text_or_none(_first(event.get("UID")))
        if uid is None:
            _logger.warning(
                "calendar: event %d in %r has no UID — skipped (identity needs it)",
                ordinal,
                member_name,
            )
            continue
        draft = _build_draft(event, uid, calendar_name)
        if draft.ts is None:
            _logger.warning(
                "calendar: event %d in %r has no readable DTSTART — imported undated",
                ordinal,
                member_name,
            )
        yield draft


@source(
    name="calendar",
    detect=_EXPORT_GLOB,
    kinds=(ItemKind.EVENT,),
    parser_version=1,
)
def parse(archive: Archive, ctx: ParseContext) -> Iterator[EventDraft]:
    """Yield EventDrafts from every calendar member, one streaming pass.

    A single ``*.ics`` pattern pass keeps tar archives sequential; members
    outside a Calendar/ folder are skipped unopened. Calendars are small
    (the largest real member is ~1 MB), so each member is decoded whole.
    ctx is part of the plugin contract but unused: there is nothing to
    parallelize.
    """
    for member, stream in archive.iter_members("*.ics"):
        if not _EXPORT_GLOB.matches(member.name):
            continue
        yield from _parse_calendar(stream.read(), member.name)
