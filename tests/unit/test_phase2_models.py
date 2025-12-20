"""Tests for Phase 2 models."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from potluck.models.base import SourceType
from potluck.models.browsing import Bookmark, BookmarkFolder, BrowsingHistory
from potluck.models.email import Email, EmailAttachment, EmailFolder, EmailThread
from potluck.models.events import (
    CalendarEvent,
    EventParticipant,
    EventStatus,
    EventVisibility,
    ResponseStatus,
)
from potluck.models.financial import (
    Account,
    AccountType,
    Budget,
    Transaction,
    TransactionType,
)
from potluck.models.links import EntityLink, EntityType, LinkType
from potluck.models.locations import Location, LocationHistory, LocationType, LocationVisit
from potluck.models.media import EmbeddingType, Media, MediaEmbedding, MediaPersonLink, MediaType
from potluck.models.messages import (
    ChatMessage,
    ChatThread,
    ChatThreadParticipant,
    MessageType,
    ThreadType,
)
from potluck.models.notes import KnowledgeNote, NoteChecklist, NoteType
from potluck.models.people import AliasType, Person, PersonAlias
from potluck.models.social import (
    Platform,
    PostType,
    SocialComment,
    SocialPost,
    Subscription,
    SubscriptionType,
)
from potluck.models.sources import ImportRun, ImportSource, ImportStatus
from potluck.models.tags import Tag, TagAssignment, TagSynonym


class TestImportSourceModels:
    """Tests for ImportSource and ImportRun models."""

    def test_import_source_creation(self) -> None:
        """ImportSource can be created with required fields."""
        source = ImportSource(
            source_type=SourceType.GOOGLE_TAKEOUT,
            name="My Google Takeout",
        )
        assert isinstance(source.id, UUID)
        assert source.source_type == SourceType.GOOGLE_TAKEOUT
        assert source.name == "My Google Takeout"
        assert source.is_active is True
        assert source.description is None
        assert source.config is None

    def test_import_source_optional_fields(self) -> None:
        """ImportSource optional fields can be set."""
        source = ImportSource(
            source_type=SourceType.REDDIT,
            name="Reddit Export",
            description="My Reddit data",
            config='{"username": "test"}',
            is_active=False,
        )
        assert source.description == "My Reddit data"
        assert source.config == '{"username": "test"}'
        assert source.is_active is False

    def test_import_run_creation(self) -> None:
        """ImportRun can be created with required fields."""
        source_id = uuid4()
        run = ImportRun(source_id=source_id)
        assert isinstance(run.id, UUID)
        assert run.source_id == source_id
        assert run.status == ImportStatus.PENDING
        assert run.entities_found == 0
        assert run.entities_created == 0

    def test_import_run_status_enum(self) -> None:
        """ImportStatus enum has expected values."""
        expected = {"pending", "running", "completed", "failed", "cancelled"}
        actual = {s.value for s in ImportStatus}
        assert actual == expected

    def test_import_run_is_running_property(self) -> None:
        """is_running property returns correct value."""
        run = ImportRun(source_id=uuid4(), status=ImportStatus.RUNNING)
        assert run.is_running is True

        run.status = ImportStatus.COMPLETED
        assert run.is_running is False

    def test_import_run_is_finished_property(self) -> None:
        """is_finished property returns correct value."""
        run = ImportRun(source_id=uuid4(), status=ImportStatus.PENDING)
        assert run.is_finished is False

        for status in [ImportStatus.COMPLETED, ImportStatus.FAILED, ImportStatus.CANCELLED]:
            run.status = status
            assert run.is_finished is True

    def test_import_run_progress_percent(self) -> None:
        """progress_percent calculates correctly."""
        run = ImportRun(source_id=uuid4(), progress_current=50, progress_total=100)
        assert run.progress_percent == 50.0

        run.progress_total = None
        assert run.progress_percent is None

        run.progress_total = 0
        assert run.progress_percent is None


class TestPeopleModels:
    """Tests for Person, PersonAlias, and FaceEncoding models."""

    def test_person_creation(self) -> None:
        """Person can be created with display_name."""
        person = Person(display_name="John Doe")
        assert isinstance(person.id, UUID)
        assert person.display_name == "John Doe"
        assert person.is_self is False
        assert person.is_merged is False

    def test_person_optional_fields(self) -> None:
        """Person optional fields can be set."""
        person = Person(
            display_name="Jane Doe",
            date_of_birth=date(1990, 5, 15),
            notes="A friend",
            is_self=True,
        )
        assert person.date_of_birth == date(1990, 5, 15)
        assert person.notes == "A friend"
        assert person.is_self is True

    def test_person_merged_property(self) -> None:
        """is_merged property returns correct value."""
        person = Person(display_name="Original")
        assert person.is_merged is False

        person.merged_into_id = uuid4()
        assert person.is_merged is True

    def test_person_alias_creation(self) -> None:
        """PersonAlias can be created with required fields."""
        person_id = uuid4()
        alias = PersonAlias(
            person_id=person_id,
            alias_type=AliasType.EMAIL,
            value="john@example.com",
            source_type=SourceType.GOOGLE_TAKEOUT,
        )
        assert alias.person_id == person_id
        assert alias.alias_type == AliasType.EMAIL
        assert alias.value == "john@example.com"
        assert alias.confidence == 1.0
        assert alias.is_primary is False

    def test_alias_type_enum(self) -> None:
        """AliasType enum has expected values."""
        expected = {"name", "email", "phone", "username", "social_handle"}
        actual = {t.value for t in AliasType}
        assert actual == expected

    def test_person_alias_normalized_value(self) -> None:
        """PersonAlias can have normalized value for matching."""
        alias = PersonAlias(
            person_id=uuid4(),
            alias_type=AliasType.EMAIL,
            value="John.Doe@Example.COM",
            normalized_value="john.doe@example.com",
            source_type=SourceType.MANUAL,
        )
        assert alias.normalized_value == "john.doe@example.com"


class TestMediaModels:
    """Tests for Media and MediaEmbedding models."""

    def test_media_creation(self) -> None:
        """Media can be created with required fields."""
        media = Media(
            source_type=SourceType.GOOGLE_TAKEOUT,
            file_path="/path/to/photo.jpg",
        )
        assert isinstance(media.id, UUID)
        assert media.file_path == "/path/to/photo.jpg"
        assert media.media_type == MediaType.OTHER
        assert media.has_text_content is False

    def test_media_type_enum(self) -> None:
        """MediaType enum has expected values."""
        expected = {"image", "video", "audio", "document", "other"}
        actual = {t.value for t in MediaType}
        assert actual == expected

    def test_media_has_text_content_property(self) -> None:
        """has_text_content property returns correct value."""
        media = Media(source_type=SourceType.MANUAL, file_path="/test.jpg")
        assert media.has_text_content is False

        media.ocr_text = "Some text"
        assert media.has_text_content is True

        media.ocr_text = None
        media.caption = "A caption"
        assert media.has_text_content is True

    def test_media_geolocated_fields(self) -> None:
        """Media inherits geolocation fields from GeolocatedEntity."""
        media = Media(
            source_type=SourceType.GOOGLE_TAKEOUT,
            file_path="/photo.jpg",
            latitude=40.7128,
            longitude=-74.0060,
            location_name="New York, NY",
        )
        assert media.has_location is True
        assert media.latitude == 40.7128
        assert media.longitude == -74.0060

    def test_media_embedding_creation(self) -> None:
        """MediaEmbedding can be created."""
        media_id = uuid4()
        embedding = MediaEmbedding(
            media_id=media_id,
            embedding_type=EmbeddingType.CLIP,
            model_name="openai/clip-vit-base-patch32",
            embedding=[0.1] * 768,
        )
        assert embedding.media_id == media_id
        assert embedding.embedding_type == EmbeddingType.CLIP
        assert len(embedding.embedding) == 768

    def test_embedding_type_enum(self) -> None:
        """EmbeddingType enum has expected values."""
        expected = {"clip", "ocr", "caption", "audio_transcript"}
        actual = {t.value for t in EmbeddingType}
        assert actual == expected

    def test_media_person_link_creation(self) -> None:
        """MediaPersonLink can be created."""
        link = MediaPersonLink(
            media_id=uuid4(),
            person_id=uuid4(),
            source_type=SourceType.GOOGLE_TAKEOUT,
        )
        assert link.confidence == 1.0
        assert link.is_confirmed is False


class TestMessageModels:
    """Tests for ChatThread and ChatMessage models."""

    def test_chat_thread_creation(self) -> None:
        """ChatThread can be created."""
        thread = ChatThread(source_type="whatsapp")
        assert isinstance(thread.id, UUID)
        assert thread.source_type == "whatsapp"
        assert thread.thread_type == ThreadType.DIRECT
        assert thread.message_count == 0
        assert thread.is_archived is False

    def test_thread_type_enum(self) -> None:
        """ThreadType enum has expected values."""
        expected = {"direct", "group", "channel", "community"}
        actual = {t.value for t in ThreadType}
        assert actual == expected

    def test_chat_message_creation(self) -> None:
        """ChatMessage can be created."""
        thread_id = uuid4()
        message = ChatMessage(
            source_type=SourceType.WHATSAPP,
            thread_id=thread_id,
            content="Hello!",
        )
        assert message.thread_id == thread_id
        assert message.content == "Hello!"
        assert message.message_type == MessageType.TEXT
        assert message.is_from_me is False

    def test_message_type_enum(self) -> None:
        """MessageType enum has expected values."""
        expected = {
            "text",
            "image",
            "video",
            "audio",
            "document",
            "sticker",
            "location",
            "contact",
            "poll",
            "system",
            "deleted",
            "other",
        }
        actual = {t.value for t in MessageType}
        assert actual == expected

    def test_chat_thread_participant_creation(self) -> None:
        """ChatThreadParticipant can be created."""
        participant = ChatThreadParticipant(
            thread_id=uuid4(),
            person_id=uuid4(),
            role="admin",
        )
        assert participant.role == "admin"
        assert participant.is_active is True


class TestEmailModels:
    """Tests for Email, EmailThread, and EmailAttachment models."""

    def test_email_thread_creation(self) -> None:
        """EmailThread can be created."""
        thread = EmailThread(source_type="google_takeout")
        assert isinstance(thread.id, UUID)
        assert thread.email_count == 0
        assert thread.is_read is False

    def test_email_creation(self) -> None:
        """Email can be created with required fields."""
        email = Email(
            source_type=SourceType.GOOGLE_TAKEOUT,
            from_address="sender@example.com",
        )
        assert email.from_address == "sender@example.com"
        assert email.folder == EmailFolder.ALL_MAIL
        assert email.has_attachments is False

    def test_email_folder_enum(self) -> None:
        """EmailFolder enum has expected values."""
        expected = {
            "inbox",
            "sent",
            "drafts",
            "trash",
            "spam",
            "archive",
            "all_mail",
            "starred",
            "important",
            "custom",
        }
        actual = {f.value for f in EmailFolder}
        assert actual == expected

    def test_email_attachment_creation(self) -> None:
        """EmailAttachment can be created."""
        attachment = EmailAttachment(
            email_id=uuid4(),
            filename="document.pdf",
        )
        assert attachment.filename == "document.pdf"
        assert attachment.is_inline is False


class TestSocialModels:
    """Tests for SocialPost, SocialComment, and Subscription models."""

    def test_social_post_creation(self) -> None:
        """SocialPost can be created."""
        post = SocialPost(
            source_type=SourceType.REDDIT,
            platform=Platform.REDDIT,
            title="My Post",
        )
        assert post.platform == Platform.REDDIT
        assert post.post_type == PostType.TEXT
        assert post.is_nsfw is False
        assert post.is_own_post is False

    def test_platform_enum(self) -> None:
        """Platform enum has expected values."""
        expected = {
            "reddit",
            "youtube",
            "twitter",
            "facebook",
            "instagram",
            "linkedin",
            "tiktok",
            "other",
        }
        actual = {p.value for p in Platform}
        assert actual == expected

    def test_post_type_enum(self) -> None:
        """PostType enum has expected values."""
        expected = {"text", "link", "image", "video", "poll", "crosspost", "other"}
        actual = {t.value for t in PostType}
        assert actual == expected

    def test_social_comment_creation(self) -> None:
        """SocialComment can be created."""
        comment = SocialComment(
            source_type=SourceType.REDDIT,
            platform=Platform.REDDIT,
            body="Great post!",
        )
        assert comment.body == "Great post!"
        assert comment.depth == 0
        assert comment.is_own_comment is False

    def test_subscription_creation(self) -> None:
        """Subscription can be created."""
        sub = Subscription(
            platform=Platform.YOUTUBE,
            subscription_type=SubscriptionType.CHANNEL,
            target_name="Tech Channel",
        )
        assert sub.platform == Platform.YOUTUBE
        assert sub.target_name == "Tech Channel"
        assert sub.is_active is True

    def test_subscription_type_enum(self) -> None:
        """SubscriptionType enum has expected values."""
        expected = {"subreddit", "user", "channel", "page", "hashtag", "topic", "other"}
        actual = {t.value for t in SubscriptionType}
        assert actual == expected


class TestBrowsingModels:
    """Tests for BrowsingHistory and Bookmark models."""

    def test_browsing_history_creation(self) -> None:
        """BrowsingHistory can be created."""
        history = BrowsingHistory(
            source_type=SourceType.GOOGLE_TAKEOUT,
            url="https://example.com",
        )
        assert history.url == "https://example.com"
        assert history.visit_count == 1

    def test_bookmark_creation(self) -> None:
        """Bookmark can be created."""
        bookmark = Bookmark(
            source_type="chrome",
            url="https://example.com",
            title="Example Site",
        )
        assert bookmark.url == "https://example.com"
        assert bookmark.title == "Example Site"
        assert bookmark.is_favorite is False

    def test_bookmark_folder_creation(self) -> None:
        """BookmarkFolder can be created."""
        folder = BookmarkFolder(
            source_type="chrome",
            name="Tech",
        )
        assert folder.name == "Tech"
        assert folder.parent_id is None


class TestNotesModels:
    """Tests for KnowledgeNote and NoteChecklist models."""

    def test_knowledge_note_creation(self) -> None:
        """KnowledgeNote can be created."""
        note = KnowledgeNote(
            source_type=SourceType.GOOGLE_TAKEOUT,
            title="My Note",
            content="Note content here",
        )
        assert note.title == "My Note"
        assert note.content == "Note content here"
        assert note.note_type == NoteType.NOTE
        assert note.is_task is False

    def test_note_type_enum(self) -> None:
        """NoteType enum has expected values."""
        expected = {
            "note",
            "task",
            "journal",
            "document",
            "snippet",
            "quote",
            "idea",
            "reference",
            "other",
        }
        actual = {t.value for t in NoteType}
        assert actual == expected

    def test_knowledge_note_task_fields(self) -> None:
        """KnowledgeNote task fields work correctly."""
        note = KnowledgeNote(
            source_type=SourceType.MANUAL,
            note_type=NoteType.TASK,
            is_task=True,
            is_completed=True,
            completed_at=datetime.now(UTC),
        )
        assert note.is_task is True
        assert note.is_completed is True

    def test_note_checklist_creation(self) -> None:
        """NoteChecklist can be created."""
        checklist = NoteChecklist(
            note_id=uuid4(),
            content="Buy groceries",
            is_checked=False,
            position=0,
        )
        assert checklist.content == "Buy groceries"
        assert checklist.is_checked is False


class TestLocationModels:
    """Tests for Location and LocationVisit models."""

    def test_location_creation(self) -> None:
        """Location can be created."""
        location = Location(
            source_type="google_takeout",
            name="Home",
            latitude=37.7749,
            longitude=-122.4194,
        )
        assert location.name == "Home"
        assert location.location_type == LocationType.OTHER
        assert location.latitude == 37.7749

    def test_location_type_enum(self) -> None:
        """LocationType enum has expected values."""
        expected = {
            "home",
            "work",
            "school",
            "gym",
            "restaurant",
            "store",
            "transit",
            "airport",
            "hotel",
            "attraction",
            "other",
        }
        actual = {t.value for t in LocationType}
        assert actual == expected

    def test_location_visit_creation(self) -> None:
        """LocationVisit can be created."""
        visit = LocationVisit(
            source_type="google_takeout",
            latitude=37.7749,
            longitude=-122.4194,
            started_at=datetime.now(UTC),
        )
        assert visit.latitude == 37.7749
        assert visit.duration_minutes is None

    def test_location_history_creation(self) -> None:
        """LocationHistory can be created."""
        history = LocationHistory(
            source_type="google_takeout",
            latitude=37.7749,
            longitude=-122.4194,
            timestamp=datetime.now(UTC),
        )
        assert history.latitude == 37.7749


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


class TestFinancialModels:
    """Tests for Account, Transaction, and Budget models."""

    def test_account_creation(self) -> None:
        """Account can be created."""
        account = Account(
            source_type="ynab",
            name="Checking Account",
        )
        assert account.name == "Checking Account"
        assert account.account_type == AccountType.CHECKING
        assert account.currency == "USD"
        assert account.is_closed is False

    def test_account_type_enum(self) -> None:
        """AccountType enum has expected values."""
        expected = {
            "checking",
            "savings",
            "credit_card",
            "cash",
            "investment",
            "loan",
            "mortgage",
            "other",
        }
        actual = {t.value for t in AccountType}
        assert actual == expected

    def test_transaction_creation(self) -> None:
        """Transaction can be created."""
        transaction = Transaction(
            source_type="ynab",
            account_id=uuid4(),
            occurred_at=datetime.now(UTC),
            amount=Decimal("-50.00"),
        )
        assert transaction.amount == Decimal("-50.00")
        assert transaction.transaction_type == TransactionType.EXPENSE
        assert transaction.is_cleared is False

    def test_transaction_type_enum(self) -> None:
        """TransactionType enum has expected values."""
        expected = {"income", "expense", "transfer", "refund", "adjustment"}
        actual = {t.value for t in TransactionType}
        assert actual == expected

    def test_budget_creation(self) -> None:
        """Budget can be created."""
        budget = Budget(
            source_type="ynab",
            year=2024,
            month=6,
            category="Groceries",
            budgeted=Decimal("500.00"),
        )
        assert budget.year == 2024
        assert budget.month == 6
        assert budget.budgeted == Decimal("500.00")

    def test_budget_month_validation(self) -> None:
        """Budget month must be between 1 and 12."""
        # Valid months work
        Budget(source_type="ynab", year=2024, month=1, category="Test", budgeted=Decimal("100"))
        Budget(source_type="ynab", year=2024, month=12, category="Test", budgeted=Decimal("100"))

        # Invalid months raise error
        with pytest.raises(ValidationError):
            Budget.model_validate(
                {
                    "source_type": "ynab",
                    "year": 2024,
                    "month": 13,
                    "category": "Test",
                    "budgeted": Decimal("100"),
                }
            )
        with pytest.raises(ValidationError):
            Budget.model_validate(
                {
                    "source_type": "ynab",
                    "year": 2024,
                    "month": 0,
                    "category": "Test",
                    "budgeted": Decimal("100"),
                }
            )


class TestEntityLinkModels:
    """Tests for EntityLink model."""

    def test_entity_link_creation(self) -> None:
        """EntityLink can be created."""
        link = EntityLink(
            source_type=EntityType.MEDIA,
            source_id=uuid4(),
            target_type=EntityType.PERSON,
            target_id=uuid4(),
            link_type=LinkType.MENTIONS,
        )
        assert link.link_type == LinkType.MENTIONS
        assert link.confidence == 1.0
        assert link.is_automatic is True
        assert link.is_confirmed is False

    def test_link_type_enum(self) -> None:
        """LinkType enum has expected values."""
        expected = {
            "same_time",
            "before",
            "after",
            "during",
            "same_location",
            "near",
            "related",
            "similar",
            "references",
            "reply_to",
            "quote",
            "mentions",
            "about",
            "sent_by",
            "received_by",
            "custom",
        }
        actual = {t.value for t in LinkType}
        assert actual == expected

    def test_entity_type_enum(self) -> None:
        """EntityType enum has expected values."""
        expected = {
            "media",
            "chat_message",
            "email",
            "social_post",
            "social_comment",
            "knowledge_note",
            "calendar_event",
            "transaction",
            "location_visit",
            "browsing_history",
            "bookmark",
            "person",
        }
        actual = {t.value for t in EntityType}
        assert actual == expected

    def test_entity_link_is_bidirectional(self) -> None:
        """is_bidirectional property returns correct value."""
        # Bidirectional links
        for link_type in [LinkType.SAME_TIME, LinkType.SAME_LOCATION, LinkType.RELATED]:
            link = EntityLink(
                source_type=EntityType.MEDIA,
                source_id=uuid4(),
                target_type=EntityType.MEDIA,
                target_id=uuid4(),
                link_type=link_type,
            )
            assert link.is_bidirectional is True

        # Directional links
        for link_type in [LinkType.BEFORE, LinkType.AFTER, LinkType.MENTIONS]:
            link = EntityLink(
                source_type=EntityType.MEDIA,
                source_id=uuid4(),
                target_type=EntityType.MEDIA,
                target_id=uuid4(),
                link_type=link_type,
            )
            assert link.is_bidirectional is False


class TestTagModels:
    """Tests for Tag, TagAssignment, and TagSynonym models."""

    def test_tag_creation(self) -> None:
        """Tag can be created."""
        tag = Tag(name="python")
        assert tag.name == "python"
        assert tag.usage_count == 0
        assert tag.is_system is False
        assert tag.is_hidden is False

    def test_tag_optional_fields(self) -> None:
        """Tag optional fields can be set."""
        tag = Tag(
            name="tech",
            display_name="Tech",
            description="Technology related",
            color="#3498db",
            icon="laptop",
        )
        assert tag.display_name == "Tech"
        assert tag.description == "Technology related"
        assert tag.color == "#3498db"

    def test_tag_assignment_creation(self) -> None:
        """TagAssignment can be created."""
        assignment = TagAssignment(
            tag_id=uuid4(),
            entity_type=EntityType.MEDIA,
            entity_id=uuid4(),
        )
        assert assignment.is_automatic is False
        assert assignment.confidence == 1.0

    def test_tag_synonym_creation(self) -> None:
        """TagSynonym can be created."""
        synonym = TagSynonym(
            tag_id=uuid4(),
            synonym="py",
        )
        assert synonym.synonym == "py"
