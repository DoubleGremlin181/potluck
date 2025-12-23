"""SQLModel entities for Potluck."""

from potluck.models.base import (
    BaseEntity,
    FlexibleEntity,
    GeolocatedEntity,
    SimpleEntity,
    SourceTrackedEntity,
    SourceType,
    TimestampedEntity,
    TimestampPrecision,
)
from potluck.models.browsing import (
    Bookmark,
    BookmarkFolder,
    BrowsingHistory,
)
from potluck.models.calendar import (
    CalendarEvent,
    EventParticipant,
    EventStatus,
    EventVisibility,
    ResponseStatus,
)
from potluck.models.email import (
    Email,
    EmailAttachment,
    EmailFolder,
    EmailThread,
)
from potluck.models.financial import (
    Account,
    AccountType,
    Budget,
    Transaction,
    TransactionType,
)
from potluck.models.links import (
    EntityLink,
    EntityType,
    LinkType,
)
from potluck.models.locations import (
    Location,
    LocationHistory,
    LocationType,
    LocationVisit,
)
from potluck.models.media import (
    EmbeddingType,
    Media,
    MediaEmbedding,
    MediaPersonLink,
    MediaType,
)
from potluck.models.messages import (
    ChatMessage,
    ChatThread,
    ChatThreadParticipant,
    MessageType,
    ThreadType,
)
from potluck.models.notes import (
    KnowledgeNote,
    NoteChecklist,
    NoteType,
)
from potluck.models.people import (
    AliasType,
    FaceEncoding,
    Person,
    PersonAlias,
)
from potluck.models.social import (
    Platform,
    PostType,
    SocialComment,
    SocialPost,
    Subscription,
    SubscriptionType,
)
from potluck.models.sources import (
    ImportRun,
    ImportSource,
    ImportStatus,
)
from potluck.models.tags import (
    Tag,
    TagAssignment,
    TagSynonym,
)

__all__ = [
    # Base
    "BaseEntity",
    "FlexibleEntity",
    "SimpleEntity",
    "SourceTrackedEntity",
    "TimestampedEntity",
    "GeolocatedEntity",
    "SourceType",
    "TimestampPrecision",
    # Browsing
    "Bookmark",
    "BookmarkFolder",
    "BrowsingHistory",
    # Email
    "Email",
    "EmailAttachment",
    "EmailFolder",
    "EmailThread",
    # Calendar
    "CalendarEvent",
    "EventParticipant",
    "EventStatus",
    "EventVisibility",
    "ResponseStatus",
    # Financial
    "Account",
    "AccountType",
    "Budget",
    "Transaction",
    "TransactionType",
    # Links
    "EntityLink",
    "EntityType",
    "LinkType",
    # Locations
    "Location",
    "LocationHistory",
    "LocationType",
    "LocationVisit",
    # Media
    "EmbeddingType",
    "Media",
    "MediaEmbedding",
    "MediaPersonLink",
    "MediaType",
    # Messages
    "ChatMessage",
    "ChatThread",
    "ChatThreadParticipant",
    "MessageType",
    "ThreadType",
    # Notes
    "KnowledgeNote",
    "NoteChecklist",
    "NoteType",
    # People
    "AliasType",
    "FaceEncoding",
    "Person",
    "PersonAlias",
    # Social
    "Platform",
    "PostType",
    "SocialComment",
    "SocialPost",
    "Subscription",
    "SubscriptionType",
    # Sources
    "ImportRun",
    "ImportSource",
    "ImportStatus",
    # Tags
    "Tag",
    "TagAssignment",
    "TagSynonym",
]
