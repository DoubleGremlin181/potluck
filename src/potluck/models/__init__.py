"""SQLModel entities for Potluck.

Entity hierarchy:
    SQLModel
      -> SimpleEntity (id, created_at, updated_at) -- for auxiliary tables
        -> BaseEntity (+ source_type, source_id, content_hash) -- for source-tracked entities
          -> TimestampedEntity (+ occurred_at, precision, timezone) -- for temporal entities
            -> GeolocatedEntity (+ lat, lon, altitude) -- for spatial entities

All entities are imported here for convenient access and to ensure SQLModel
metadata registration. Use ``register_models()`` to trigger table creation
(e.g., in Alembic migrations) and ``get_entity_type_model_map()`` to resolve
an EntityType enum to its model class at runtime.
"""

from sqlmodel import SQLModel

from potluck.models.base import (
    BaseEntity,
    EntityType,
    GeolocatedEntity,
    IngestableEntity,
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
from potluck.models.documents import Document
from potluck.models.email import (
    Email,
    EmailAttachment,
    EmailFolder,
    EmailThread,
)
from potluck.models.faces import (
    ClusterStatus,
    FaceCluster,
    MediaPersonLink,
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
    FaceEncoding,
    Person,
    PersonAlias,
)
from potluck.models.social import (
    Platform,
    PostType,
    SocialComment,
    SocialFollow,
    SocialFollowType,
    SocialPost,
)
from potluck.models.sources import (
    ImportRun,
    ImportSource,
    ImportStatus,
    ProcessingProgress,
    StageType,
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
    EntityType.DOCUMENT: Document,
    EntityType.CALENDAR_EVENT: CalendarEvent,
    EntityType.TRANSACTION: Transaction,
    EntityType.LOCATION: Location,
    EntityType.LOCATION_VISIT: LocationVisit,
    EntityType.BROWSING_HISTORY: BrowsingHistory,
    EntityType.BOOKMARK: Bookmark,
    EntityType.SOCIAL_FOLLOW: SocialFollow,
    EntityType.BUDGET: Budget,
    EntityType.PERSON: Person,
    EntityType.TAG: Tag,
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
    "IngestableEntity",
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
    # Documents
    "Document",
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
    "SocialFollow",
    "SocialFollowType",
    # Sources
    "ImportRun",
    "ImportSource",
    "ImportStatus",
    "ProcessingProgress",
    "StageType",
    # Tags
    "Tag",
    "TagAssignment",
]
