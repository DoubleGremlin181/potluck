"""SQLModel entities for Potluck."""

from sqlmodel import SQLModel

from potluck.models.base import (
    BaseEntity,
    EntityType,
    GeolocatedEntity,
    SimpleEntity,
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
from potluck.models.notes import KnowledgeNote
from potluck.models.people import (
    AliasType,
    ClusterStatus,
    FaceCluster,
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
)
from potluck.models.utils import (
    IANATimezone,
    UTCDatetime,
    ensure_utc,
    utc_now,
)


def register_models() -> list[str]:
    """Ensure all models are loaded and return list of registered model names.

    Call this function to trigger model imports and register all tables
    with SQLModel.metadata. Useful for Alembic migrations.

    Returns:
        List of registered model class names.
    """
    return __all__


# EntityType to Model class mapping - all models are already imported above
_ENTITY_TYPE_MODEL_MAP: dict[EntityType, type[SQLModel]] = {
    EntityType.MEDIA: Media,
    EntityType.CHAT_MESSAGE: ChatMessage,
    EntityType.EMAIL: Email,
    EntityType.SOCIAL_POST: SocialPost,
    EntityType.SOCIAL_COMMENT: SocialComment,
    EntityType.KNOWLEDGE_NOTE: KnowledgeNote,
    EntityType.CALENDAR_EVENT: CalendarEvent,
    EntityType.TRANSACTION: Transaction,
    EntityType.LOCATION_VISIT: LocationVisit,
    EntityType.BROWSING_HISTORY: BrowsingHistory,
    EntityType.BOOKMARK: Bookmark,
    EntityType.PERSON: Person,
}


def get_entity_type_model_map() -> dict[EntityType, type[SQLModel]]:
    """Get mapping from EntityType to model class.

    Returns:
        Dict mapping EntityType enum values to their corresponding model classes.
    """
    return _ENTITY_TYPE_MODEL_MAP


__all__ = [
    # Base
    "BaseEntity",
    "SimpleEntity",
    "TimestampedEntity",
    "GeolocatedEntity",
    "SourceType",
    "TimestampPrecision",
    "EntityType",
    "get_entity_type_model_map",
    # Utils
    "utc_now",
    "ensure_utc",
    "UTCDatetime",
    "IANATimezone",
    # Functions
    "register_models",
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
    # People
    "AliasType",
    "ClusterStatus",
    "FaceCluster",
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
]
