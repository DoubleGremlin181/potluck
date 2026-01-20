"""Tests for Google Calendar event ingestion."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from potluck.models.base import SourceType
from potluck.models.calendar import (
    CalendarEvent,
    EventParticipant,
    EventStatus,
    EventVisibility,
    ResponseStatus,
)
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.ingestion.google_takeout.calendar import (
    _extract_email,
    _map_response_status,
    _map_status,
    _map_visibility,
    ingest_calendar_events,
)

# Path to test fixtures
FIXTURES_PATH = Path(__file__).parent.parent.parent.parent.parent / "fixtures" / "google_takeout"


class TestCalendarIngestion:
    """Tests for Google Calendar ingestion."""

    def test_ingest_calendar_from_fixtures(self) -> None:
        """Ingest calendar events from fixture files."""
        entities = list(ingest_calendar_events(FIXTURES_PATH))

        # Separate events and participants
        events = [e for e in entities if isinstance(e, CalendarEvent)]
        participants = [e for e in entities if isinstance(e, EventParticipant)]

        # Should have 5 events
        assert len(events) == 5

        # Should have 3 participants (2 from event1, 1 from event3)
        assert len(participants) == 3

    def test_calendar_name_from_header(self) -> None:
        """Calendar name is extracted from X-WR-CALNAME header."""
        entities = list(ingest_calendar_events(FIXTURES_PATH))
        events = [e for e in entities if isinstance(e, CalendarEvent)]

        # All events should have the calendar name from the header
        for event in events:
            assert event.calendar_name == "Test Calendar"

    def test_timed_event_properties(self) -> None:
        """Timed events have correct time properties."""
        entities = list(ingest_calendar_events(FIXTURES_PATH))
        events = [e for e in entities if isinstance(e, CalendarEvent)]

        # Find the Team Standup event
        standup = next((e for e in events if e.summary == "Team Standup"), None)
        assert standup is not None
        assert standup.is_all_day is False
        assert standup.start_time.hour == 15  # 10 AM EST = 15:00 UTC
        assert standup.end_time is not None
        assert standup.end_time.hour == 16  # 11 AM EST = 16:00 UTC
        assert standup.timezone == "America/New_York"

    def test_all_day_event_properties(self) -> None:
        """All-day events are correctly identified."""
        entities = list(ingest_calendar_events(FIXTURES_PATH))
        events = [e for e in entities if isinstance(e, CalendarEvent)]

        # Find the Company Holiday event
        holiday = next((e for e in events if e.summary == "Company Holiday"), None)
        assert holiday is not None
        assert holiday.is_all_day is True
        assert holiday.start_time.year == 2024
        assert holiday.start_time.month == 1
        assert holiday.start_time.day == 20
        assert holiday.visibility == EventVisibility.PUBLIC

    def test_recurring_event_properties(self) -> None:
        """Recurring events have recurrence rule."""
        entities = list(ingest_calendar_events(FIXTURES_PATH))
        events = [e for e in entities if isinstance(e, CalendarEvent)]

        # Find the Weekly Sync event
        weekly = next((e for e in events if e.summary == "Weekly Sync"), None)
        assert weekly is not None
        assert weekly.is_recurring is True
        assert weekly.recurrence_rule is not None
        assert "FREQ=WEEKLY" in weekly.recurrence_rule
        assert "BYDAY=MO" in weekly.recurrence_rule

    def test_event_status_mapping(self) -> None:
        """Event status is correctly mapped."""
        entities = list(ingest_calendar_events(FIXTURES_PATH))
        events = [e for e in entities if isinstance(e, CalendarEvent)]

        # Find events by status
        cancelled = next((e for e in events if e.summary == "Cancelled Meeting"), None)
        tentative = next((e for e in events if e.summary == "Tentative Planning"), None)
        confirmed = next((e for e in events if e.summary == "Team Standup"), None)

        assert cancelled is not None
        assert cancelled.status == EventStatus.CANCELLED

        assert tentative is not None
        assert tentative.status == EventStatus.TENTATIVE
        assert tentative.visibility == EventVisibility.PRIVATE

        assert confirmed is not None
        assert confirmed.status == EventStatus.CONFIRMED

    def test_organizer_info(self) -> None:
        """Organizer information is extracted correctly."""
        entities = list(ingest_calendar_events(FIXTURES_PATH))
        events = [e for e in entities if isinstance(e, CalendarEvent)]

        # Find the Team Standup event
        standup = next((e for e in events if e.summary == "Team Standup"), None)
        assert standup is not None
        assert standup.organizer_email == "john@example.com"
        assert standup.organizer_name == "John Doe"

    def test_event_location_and_description(self) -> None:
        """Location and description are extracted."""
        entities = list(ingest_calendar_events(FIXTURES_PATH))
        events = [e for e in entities if isinstance(e, CalendarEvent)]

        standup = next((e for e in events if e.summary == "Team Standup"), None)
        assert standup is not None
        assert standup.location_text == "Conference Room A"
        assert standup.description == "Daily standup meeting for the development team"

    def test_conference_url_extraction(self) -> None:
        """Conference URLs are extracted when present."""
        entities = list(ingest_calendar_events(FIXTURES_PATH))
        events = [e for e in entities if isinstance(e, CalendarEvent)]

        standup = next((e for e in events if e.summary == "Team Standup"), None)
        assert standup is not None
        assert standup.conference_url == "https://meet.google.com/abc-defg-hij"

    def test_event_timestamps(self) -> None:
        """Created and updated timestamps are extracted."""
        entities = list(ingest_calendar_events(FIXTURES_PATH))
        events = [e for e in entities if isinstance(e, CalendarEvent)]

        standup = next((e for e in events if e.summary == "Team Standup"), None)
        assert standup is not None
        assert standup.event_created_at is not None
        assert standup.event_created_at.year == 2024
        assert standup.event_updated_at is not None
        assert standup.event_updated_at.month == 1
        assert standup.event_updated_at.day == 10

    def test_participant_extraction(self) -> None:
        """Participants are correctly extracted."""
        entities = list(ingest_calendar_events(FIXTURES_PATH))
        participants = [e for e in entities if isinstance(e, EventParticipant)]

        # Find Jane's participation
        jane = next((p for p in participants if p.email == "jane@example.com"), None)
        assert jane is not None
        assert jane.display_name == "Jane Smith"
        assert jane.response_status == ResponseStatus.ACCEPTED
        assert jane.is_optional is False

        # Find Bob's participation
        bob = next((p for p in participants if p.email == "bob@example.com"), None)
        assert bob is not None
        assert bob.display_name == "Bob Wilson"
        assert bob.response_status == ResponseStatus.TENTATIVE
        assert bob.is_optional is True

    def test_source_type(self) -> None:
        """Events have correct source type."""
        entities = list(ingest_calendar_events(FIXTURES_PATH))
        events = [e for e in entities if isinstance(e, CalendarEvent)]

        for event in events:
            assert event.source_type == SourceType.GOOGLE_TAKEOUT

    def test_content_hash_uniqueness(self) -> None:
        """Each event has a unique content hash."""
        entities = list(ingest_calendar_events(FIXTURES_PATH))
        events = [e for e in entities if isinstance(e, CalendarEvent)]

        hashes = [e.content_hash for e in events]
        assert len(hashes) == len(set(hashes))  # All unique

    def test_date_filter_since(self) -> None:
        """Date filter 'since' excludes earlier events."""
        filters = PipelineFilter(since=datetime(2024, 1, 20, tzinfo=UTC))
        entities = list(ingest_calendar_events(FIXTURES_PATH, filters))
        events = [e for e in entities if isinstance(e, CalendarEvent)]

        # Should exclude Team Standup (Jan 15)
        # Remaining: Holiday (Jan 20), Weekly Sync (Jan 22), Cancelled (Jan 25), Tentative (Jan 30)
        assert len(events) == 4

        summaries = {e.summary for e in events}
        assert "Team Standup" not in summaries
        assert "Company Holiday" in summaries

    def test_date_filter_until(self) -> None:
        """Date filter 'until' excludes later events."""
        filters = PipelineFilter(until=datetime(2024, 1, 22, tzinfo=UTC))
        entities = list(ingest_calendar_events(FIXTURES_PATH, filters))
        events = [e for e in entities if isinstance(e, CalendarEvent)]

        # Should include only Team Standup (Jan 15) and Company Holiday (Jan 20)
        assert len(events) == 2

        summaries = {e.summary for e in events}
        assert "Team Standup" in summaries
        assert "Company Holiday" in summaries

    def test_empty_directory(self) -> None:
        """Empty directory yields no entities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entities = list(ingest_calendar_events(Path(tmpdir)))
            assert entities == []

    def test_ical_uid_as_source_id(self) -> None:
        """iCal UID is used as source_id."""
        entities = list(ingest_calendar_events(FIXTURES_PATH))
        events = [e for e in entities if isinstance(e, CalendarEvent)]

        standup = next((e for e in events if e.summary == "Team Standup"), None)
        assert standup is not None
        assert standup.source_id == "event1@example.com"
        assert standup.ical_uid == "event1@example.com"


class TestHelperFunctions:
    """Tests for calendar parsing helper functions."""

    def test_extract_email_mailto(self) -> None:
        """Extract email from mailto: URI."""
        assert _extract_email("mailto:test@example.com") == "test@example.com"
        assert _extract_email("MAILTO:TEST@EXAMPLE.COM") == "TEST@EXAMPLE.COM"

    def test_extract_email_raw(self) -> None:
        """Extract raw email address."""
        assert _extract_email("test@example.com") == "test@example.com"

    def test_extract_email_invalid(self) -> None:
        """Return None for invalid email."""
        assert _extract_email("") is None
        assert _extract_email("no-at-sign") is None

    def test_map_status(self) -> None:
        """Map iCal status strings to EventStatus."""
        assert _map_status("CONFIRMED") == EventStatus.CONFIRMED
        assert _map_status("TENTATIVE") == EventStatus.TENTATIVE
        assert _map_status("CANCELLED") == EventStatus.CANCELLED
        assert _map_status("confirmed") == EventStatus.CONFIRMED
        assert _map_status(None) == EventStatus.CONFIRMED
        assert _map_status("UNKNOWN") == EventStatus.CONFIRMED

    def test_map_visibility(self) -> None:
        """Map iCal CLASS strings to EventVisibility."""
        assert _map_visibility("PUBLIC") == EventVisibility.PUBLIC
        assert _map_visibility("PRIVATE") == EventVisibility.PRIVATE
        assert _map_visibility("CONFIDENTIAL") == EventVisibility.CONFIDENTIAL
        assert _map_visibility("public") == EventVisibility.PUBLIC
        assert _map_visibility(None) == EventVisibility.DEFAULT
        assert _map_visibility("UNKNOWN") == EventVisibility.DEFAULT

    def test_map_response_status(self) -> None:
        """Map iCal PARTSTAT strings to ResponseStatus."""
        assert _map_response_status("ACCEPTED") == ResponseStatus.ACCEPTED
        assert _map_response_status("DECLINED") == ResponseStatus.DECLINED
        assert _map_response_status("TENTATIVE") == ResponseStatus.TENTATIVE
        assert _map_response_status("NEEDS-ACTION") == ResponseStatus.NEEDS_ACTION
        assert _map_response_status("accepted") == ResponseStatus.ACCEPTED
        assert _map_response_status(None) == ResponseStatus.NEEDS_ACTION


class TestIntegrationWithStage:
    """Integration tests with GoogleTakeoutStage."""

    def test_stage_executes_calendar_ingestion(self) -> None:
        """Stage correctly routes to calendar ingestion."""
        from potluck.models.base import EntityType
        from potluck.pipeline.ingestion.google_takeout import GoogleTakeoutStage

        stage = GoogleTakeoutStage()

        # Execute for calendar events only
        entities = list(
            stage.execute(
                FIXTURES_PATH,
                entity_types={EntityType.CALENDAR_EVENT},
            )
        )

        # Should get events and participants
        events = [e for e in entities if isinstance(e, CalendarEvent)]
        participants = [e for e in entities if isinstance(e, EventParticipant)]

        assert len(events) == 5
        assert len(participants) == 3
