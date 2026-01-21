"""Google Calendar event ingestion from Google Takeout.

Handles:
- Calendar/*.ics: iCalendar files with VEVENT entries

Uses the icalendar library to parse ICS files and extract calendar events
and their participants.
"""

import hashlib
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

from icalendar import Calendar
from icalendar.cal import Component

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.calendar import (
    CalendarEvent,
    EventParticipant,
    EventStatus,
    EventVisibility,
    ResponseStatus,
)
from potluck.pipeline.dtos import PipelineFilter

logger = get_logger(__name__)


def ingest_calendar_events(
    path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[CalendarEvent | EventParticipant]:
    """Ingest Google Calendar events from Google Takeout.

    Yields CalendarEvent entities first, then EventParticipant entities
    (grouped by event) for proper foreign key ordering.

    Args:
        path: Path to the extracted takeout directory.
        filters: Optional date range filters.

    Yields:
        CalendarEvent and EventParticipant entities.
    """
    calendar_dir = _find_calendar_dir(path)
    if not calendar_dir:
        logger.debug("No Calendar directory found")
        return

    # Process all ICS files
    for ics_file in sorted(calendar_dir.rglob("*.ics")):
        yield from _process_ics_file(ics_file, filters)


def _find_calendar_dir(path: Path) -> Path | None:
    """Find Google Calendar directory in takeout."""
    candidates = [
        path / "Takeout" / "Calendar",
        path / "Calendar",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _process_ics_file(
    ics_file: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[CalendarEvent | EventParticipant]:
    """Process a single ICS file and yield events and participants.

    Args:
        ics_file: Path to the ICS file.
        filters: Optional date range filters.

    Yields:
        CalendarEvent and EventParticipant entities.
    """
    try:
        content = ics_file.read_bytes()
        cal = Calendar.from_ical(content)
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to parse ICS file {ics_file}: {e}")
        return

    # Get calendar name from filename (without extension)
    calendar_name = ics_file.stem

    # X-WR-CALNAME contains the actual calendar name if present
    cal_name_prop = cal.get("x-wr-calname")
    if cal_name_prop:
        calendar_name = str(cal_name_prop)

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        event = _parse_vevent(component, calendar_name)
        if event is None:
            continue

        # Apply date filters based on start_time
        if filters:
            if filters.since and event.start_time < filters.since:
                continue
            if filters.until and event.start_time >= filters.until:
                continue

        yield event

        # Yield participants after the event
        participants = _parse_participants(component, event)
        yield from participants


def _parse_vevent(component: Component, calendar_name: str) -> CalendarEvent | None:
    """Parse a VEVENT component into a CalendarEvent.

    Args:
        component: An icalendar VEVENT component.
        calendar_name: Name of the calendar.

    Returns:
        CalendarEvent entity or None if parsing fails.
    """
    # Get required fields
    dtstart = component.get("dtstart")
    if not dtstart:
        logger.debug("Skipping event without DTSTART")
        return None

    dtstart_value = dtstart.dt

    # Determine if all-day event (date vs datetime)
    is_all_day = isinstance(dtstart_value, date) and not isinstance(dtstart_value, datetime)

    # Convert to datetime for storage
    if is_all_day:
        start_time = datetime(
            dtstart_value.year, dtstart_value.month, dtstart_value.day, tzinfo=UTC
        )
    else:
        start_time = _ensure_utc(dtstart_value)

    # Get end time
    end_time = None
    dtend = component.get("dtend")
    if dtend:
        dtend_value = dtend.dt
        if isinstance(dtend_value, date) and not isinstance(dtend_value, datetime):
            end_time = datetime(dtend_value.year, dtend_value.month, dtend_value.day, tzinfo=UTC)
        else:
            end_time = _ensure_utc(dtend_value)

    # Extract timezone from DTSTART
    timezone_str = None
    if hasattr(dtstart, "params"):
        tzid = dtstart.params.get("TZID")
        if tzid:
            timezone_str = str(tzid)

    # Get other fields
    uid = _get_str(component, "uid")
    summary = _get_str(component, "summary")
    description = _get_str(component, "description")
    location = _get_str(component, "location")
    status_str = _get_str(component, "status")
    url = _get_str(component, "url")

    # Map status
    status = _map_status(status_str)

    # Get recurrence info
    rrule = component.get("rrule")
    recurrence_rule = None
    is_recurring = False
    if rrule:
        is_recurring = True
        recurrence_rule = rrule.to_ical().decode("utf-8")

    # Recurring event ID (RECURRENCE-ID)
    recurrence_id = component.get("recurrence-id")
    recurring_event_id = None
    if recurrence_id:
        recurring_event_id = str(recurrence_id.dt)

    # Get organizer info
    organizer = component.get("organizer")
    organizer_email = None
    organizer_name = None
    if organizer:
        organizer_email = _extract_email(str(organizer))
        organizer_name = organizer.params.get("CN") if hasattr(organizer, "params") else None

    # Get created/modified timestamps
    created = component.get("created")
    event_created_at = None
    if created:
        event_created_at = _ensure_utc(created.dt)

    last_modified = component.get("last-modified")
    event_updated_at = None
    if last_modified:
        event_updated_at = _ensure_utc(last_modified.dt)

    # Get visibility (CLASS property)
    class_prop = _get_str(component, "class")
    visibility = _map_visibility(class_prop)

    # Generate source_id from UID or fallback to content-based hash
    source_id = uid
    if source_id is None:
        # Generate fallback from stable content
        fallback_parts = [summary or "", start_time.isoformat(), calendar_name or ""]
        source_id = hashlib.sha256("|".join(fallback_parts).encode()).hexdigest()[:32]

    # Generate content hash from source_id + calendar name
    hash_content = f"{source_id}{calendar_name}{start_time.isoformat()}"
    content_hash = hashlib.sha256(hash_content.encode()).hexdigest()

    return CalendarEvent(
        source_type=SourceType.GOOGLE_TAKEOUT,
        source_id=source_id,
        content_hash=content_hash,
        occurred_at=start_time,
        # Calendar-specific fields
        ical_uid=uid,
        calendar_name=calendar_name,
        summary=summary,
        description=description,
        start_time=start_time,
        end_time=end_time,
        is_all_day=is_all_day,
        timezone=timezone_str,
        is_recurring=is_recurring,
        recurrence_rule=recurrence_rule,
        recurring_event_id=recurring_event_id,
        status=status,
        visibility=visibility,
        location_text=location,
        organizer_email=organizer_email,
        organizer_name=organizer_name,
        event_created_at=event_created_at,
        event_updated_at=event_updated_at,
        conference_url=url if url and _is_conference_url(url) else None,
    )


def _parse_participants(
    component: Component,
    event: CalendarEvent,
) -> Iterator[EventParticipant]:
    """Parse ATTENDEE properties into EventParticipant entities.

    Args:
        component: An icalendar VEVENT component.
        event: The parent CalendarEvent entity.

    Yields:
        EventParticipant entities.
    """
    attendees = component.get("attendee")
    if not attendees:
        return

    # Normalize to list (single attendee is not a list)
    if not isinstance(attendees, list):
        attendees = [attendees]

    for attendee in attendees:
        email = _extract_email(str(attendee))
        if not email:
            continue

        # Get attendee parameters
        params = attendee.params if hasattr(attendee, "params") else {}
        display_name = params.get("CN")
        role = params.get("ROLE", "REQ-PARTICIPANT")
        partstat = params.get("PARTSTAT", "NEEDS-ACTION")

        # Map response status
        response_status = _map_response_status(partstat)

        # Check if optional attendee
        is_optional = role == "OPT-PARTICIPANT"

        yield EventParticipant(
            event_id=event.id,
            email=email,
            display_name=display_name,
            is_optional=is_optional,
            response_status=response_status,
        )


def _get_str(component: Component, prop: str) -> str | None:
    """Get a string property from a component."""
    value = component.get(prop)
    return str(value) if value else None


def _ensure_utc(dt: datetime | date) -> datetime:
    """Ensure a datetime is in UTC timezone."""
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return datetime(dt.year, dt.month, dt.day, tzinfo=UTC)

    if dt.tzinfo is None:
        # Assume UTC for naive datetimes
        return dt.replace(tzinfo=UTC)

    return dt.astimezone(UTC)


def _extract_email(mailto_str: str) -> str | None:
    """Extract email address from mailto: URI or raw email."""
    if not mailto_str:
        return None

    # Handle mailto: URIs
    if mailto_str.lower().startswith("mailto:"):
        return mailto_str[7:]

    # Check if it looks like an email
    if "@" in mailto_str:
        return mailto_str

    return None


def _map_status(status_str: str | None) -> EventStatus:
    """Map iCalendar STATUS to EventStatus enum."""
    if not status_str:
        return EventStatus.CONFIRMED

    status_upper = status_str.upper()
    if status_upper == "TENTATIVE":
        return EventStatus.TENTATIVE
    if status_upper == "CANCELLED":
        return EventStatus.CANCELLED
    return EventStatus.CONFIRMED


def _map_visibility(class_str: str | None) -> EventVisibility:
    """Map iCalendar CLASS to EventVisibility enum."""
    if not class_str:
        return EventVisibility.DEFAULT

    class_upper = class_str.upper()
    if class_upper == "PUBLIC":
        return EventVisibility.PUBLIC
    if class_upper == "PRIVATE":
        return EventVisibility.PRIVATE
    if class_upper == "CONFIDENTIAL":
        return EventVisibility.CONFIDENTIAL
    return EventVisibility.DEFAULT


def _map_response_status(partstat: str | None) -> ResponseStatus:
    """Map iCalendar PARTSTAT to ResponseStatus enum."""
    if not partstat:
        return ResponseStatus.NEEDS_ACTION

    partstat_upper = partstat.upper()
    if partstat_upper == "ACCEPTED":
        return ResponseStatus.ACCEPTED
    if partstat_upper == "DECLINED":
        return ResponseStatus.DECLINED
    if partstat_upper == "TENTATIVE":
        return ResponseStatus.TENTATIVE
    return ResponseStatus.NEEDS_ACTION


def _is_conference_url(url: str) -> bool:
    """Check if URL appears to be a video conference link."""
    conference_domains = [
        "meet.google.com",
        "zoom.us",
        "teams.microsoft.com",
        "webex.com",
    ]
    url_lower = url.lower()
    return any(domain in url_lower for domain in conference_domains)
