"""Tests for CalendarEvent and EventParticipant models."""

from datetime import UTC, datetime
from uuid import uuid4

from potluck.models.base import SourceType
from potluck.models.calendar import (
    CalendarEvent,
    EventParticipant,
    EventStatus,
    EventVisibility,
    ResponseStatus,
)


class TestEventModels:
    """Tests for CalendarEvent and EventParticipant models."""

    def test_calendar_event_creation(self) -> None:
        """CalendarEvent can be created."""
        event = CalendarEvent(
            source_type=SourceType.GOOGLE_TAKEOUT,
            summary="Team Meeting",
            start_time=datetime.now(UTC),
            status=EventStatus.CONFIRMED,
            visibility=EventVisibility.DEFAULT,
        )
        assert event.summary == "Team Meeting"
        assert event.status == EventStatus.CONFIRMED
        assert event.is_all_day is False

    def test_event_status_enum(self) -> None:
        """EventStatus enum has expected values."""
        expected = {"confirmed", "tentative", "cancelled"}
        actual = {s.value for s in EventStatus}
        assert actual == expected

    def test_event_visibility_enum(self) -> None:
        """EventVisibility enum has expected values."""
        expected = {"default", "public", "private", "confidential"}
        actual = {v.value for v in EventVisibility}
        assert actual == expected

    def test_response_status_enum(self) -> None:
        """ResponseStatus enum has expected values."""
        expected = {"needs_action", "accepted", "declined", "tentative"}
        actual = {s.value for s in ResponseStatus}
        assert actual == expected

    def test_event_participant_creation(self) -> None:
        """EventParticipant can be created."""
        participant = EventParticipant(
            event_id=uuid4(),
            email="attendee@example.com",
            response_status=ResponseStatus.ACCEPTED,
        )
        assert participant.email == "attendee@example.com"
        assert participant.response_status == ResponseStatus.ACCEPTED
        assert participant.is_organizer is False
