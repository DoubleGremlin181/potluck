"""Calendar event models for Google Calendar, Apple Calendar, Outlook, iCal, etc."""

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlmodel import Field, Relationship

from potluck.models.base import GeolocatedEntity, SimpleEntity
from potluck.models.utils import IANATimezone


class EventStatus(str, Enum):
    """Status of a calendar event."""

    CONFIRMED = "confirmed"
    TENTATIVE = "tentative"
    CANCELLED = "cancelled"


class EventVisibility(str, Enum):
    """Visibility of a calendar event."""

    DEFAULT = "default"
    PUBLIC = "public"
    PRIVATE = "private"
    CONFIDENTIAL = "confidential"


class ResponseStatus(str, Enum):
    """Attendee response status."""

    NEEDS_ACTION = "needs_action"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    TENTATIVE = "tentative"


class CalendarEvent(GeolocatedEntity, table=True):
    """Calendar event with timing, location, and participants.

    Supports events from various sources:
    - Google Calendar (via Takeout)
    - Apple Calendar (via iCal export)
    - Microsoft Outlook (via iCal/ICS export)
    - Generic iCal/ICS files
    """

    __tablename__ = "calendar_events"

    # Event identifiers
    event_id: str | None = Field(
        default=None,
        index=True,
        description="Calendar-specific event ID",
    )
    ical_uid: str | None = Field(
        default=None,
        index=True,
        description="iCalendar UID for cross-calendar matching",
    )
    calendar_name: str | None = Field(
        default=None,
        description="Name of the calendar containing this event",
    )

    # Event details
    summary: str | None = Field(
        default=None,
        description="Event title/summary",
    )
    description: str | None = Field(
        default=None,
        description="Event description (stored for FTS)",
    )

    # Timing - stored in UTC, use timezone field to display in original zone
    start_time: datetime = Field(
        index=True,
        description="Event start time in UTC (convert using timezone for display)",
    )
    end_time: datetime | None = Field(
        default=None,
        description="Event end time in UTC (convert using timezone for display)",
    )
    is_all_day: bool = Field(
        default=False,
        description="Whether this is an all-day event",
    )
    timezone: IANATimezone = Field(
        default=None,
        description="IANA timezone for display (e.g., 'America/New_York')",
    )

    # Recurrence
    is_recurring: bool = Field(
        default=False,
        description="Whether this is a recurring event",
    )
    recurrence_rule: str | None = Field(
        default=None,
        description="iCal RRULE for recurring events",
    )
    recurring_event_id: str | None = Field(
        default=None,
        description="ID of the parent recurring event",
    )

    # Status and visibility
    status: EventStatus = Field(
        default=EventStatus.CONFIRMED,
        description="Event status",
    )
    visibility: EventVisibility = Field(
        default=EventVisibility.DEFAULT,
        description="Event visibility",
    )

    # Location text (supplements lat/long from GeolocatedEntity)
    location_text: str | None = Field(
        default=None,
        description="Full location text (address, room name, building, etc.)",
    )

    # Organizer
    organizer_id: UUID | None = Field(
        default=None,
        foreign_key="people.id",
        description="Person who organized the event",
    )
    organizer_email: str | None = Field(
        default=None,
        description="Organizer email address",
    )
    organizer_name: str | None = Field(
        default=None,
        description="Organizer display name",
    )

    # User's response
    my_response_status: ResponseStatus | None = Field(
        default=None,
        description="The data owner's response to this event",
    )
    is_organizer: bool = Field(
        default=False,
        description="Whether the data owner is the organizer",
    )

    # Reminders
    has_reminders: bool = Field(
        default=False,
        description="Whether reminders are set",
    )
    reminder_minutes: str | None = Field(
        default=None,
        description="JSON-encoded list of reminder times in minutes before",
    )

    # Meeting link
    conference_url: str | None = Field(
        default=None,
        description="Video conference URL (Meet, Zoom, etc.)",
    )
    conference_type: str | None = Field(
        default=None,
        description="Type of conference (hangoutsMeet, zoom, etc.)",
    )

    # Attachments
    attachment_urls: str | None = Field(
        default=None,
        description="JSON-encoded list of attachment URLs",
    )

    # Timestamps from source
    event_created_at: datetime | None = Field(
        default=None,
        description="When the event was created in the calendar",
    )
    event_updated_at: datetime | None = Field(
        default=None,
        description="When the event was last updated",
    )

    # Colors/categories
    color: str | None = Field(
        default=None,
        description="Event color ID or hex code",
    )

    # Relationships
    participants: list["EventParticipant"] = Relationship(back_populates="event")


class EventParticipant(SimpleEntity, table=True):
    """Participant in a calendar event."""

    __tablename__ = "event_participants"

    event_id: UUID = Field(
        foreign_key="calendar_events.id",
        index=True,
        description="The event",
    )
    person_id: UUID | None = Field(
        default=None,
        foreign_key="people.id",
        index=True,
        description="Matched Person record",
    )

    # Participant information
    email: str | None = Field(
        default=None,
        description="Participant email address",
    )
    display_name: str | None = Field(
        default=None,
        description="Participant display name",
    )

    # Role and response
    is_organizer: bool = Field(
        default=False,
        description="Whether this participant is the organizer",
    )
    is_optional: bool = Field(
        default=False,
        description="Whether attendance is optional",
    )
    response_status: ResponseStatus = Field(
        default=ResponseStatus.NEEDS_ACTION,
        description="Participant's response",
    )

    # Comment
    comment: str | None = Field(
        default=None,
        description="Participant's comment on the event",
    )

    # Relationships
    event: CalendarEvent = Relationship(back_populates="participants")
