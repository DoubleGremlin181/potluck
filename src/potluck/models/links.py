"""Entity link models for cross-entity relationships."""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel

from potluck.models.base import _utc_now


class LinkType(str, Enum):
    """Type of entity relationship."""

    # Temporal links
    SAME_TIME = "same_time"  # Entities occurred at the same time
    BEFORE = "before"  # Source entity before target
    AFTER = "after"  # Source entity after target
    DURING = "during"  # Source entity during target

    # Spatial links
    SAME_LOCATION = "same_location"  # Entities at the same location
    NEAR = "near"  # Entities geographically close

    # Semantic links
    RELATED = "related"  # General semantic relationship
    SIMILAR = "similar"  # Content similarity
    REFERENCES = "references"  # One references the other
    REPLY_TO = "reply_to"  # Reply relationship
    QUOTE = "quote"  # Quote/citation relationship

    # Person links
    MENTIONS = "mentions"  # Entity mentions a person
    ABOUT = "about"  # Entity is about a person/topic
    SENT_BY = "sent_by"  # Sent by relationship
    RECEIVED_BY = "received_by"  # Received by relationship

    # Custom
    CUSTOM = "custom"  # User-defined relationship


class EntityType(str, Enum):
    """Types of entities that can be linked."""

    MEDIA = "media"
    CHAT_MESSAGE = "chat_message"
    EMAIL = "email"
    SOCIAL_POST = "social_post"
    SOCIAL_COMMENT = "social_comment"
    KNOWLEDGE_NOTE = "knowledge_note"
    CALENDAR_EVENT = "calendar_event"
    TRANSACTION = "transaction"
    LOCATION_VISIT = "location_visit"
    BROWSING_HISTORY = "browsing_history"
    BOOKMARK = "bookmark"
    PERSON = "person"


class EntityLink(SQLModel, table=True):
    """Cross-entity relationship link.

    Connects any two entities across different types with relationship metadata.
    Used for temporal linking, spatial proximity, semantic similarity, etc.
    """

    __tablename__ = "entity_links"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the link",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the link was created",
    )

    # Source entity
    source_type: EntityType = Field(
        description="Type of the source entity",
    )
    source_id: UUID = Field(
        index=True,
        description="ID of the source entity",
    )

    # Target entity
    target_type: EntityType = Field(
        description="Type of the target entity",
    )
    target_id: UUID = Field(
        index=True,
        description="ID of the target entity",
    )

    # Relationship
    link_type: LinkType = Field(
        description="Type of relationship between entities",
    )
    custom_type: str | None = Field(
        default=None,
        description="Custom relationship type name (when link_type=CUSTOM)",
    )

    # Link metadata
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score for automatic links (0.0-1.0)",
    )
    is_automatic: bool = Field(
        default=True,
        description="Whether this link was created automatically",
    )
    is_confirmed: bool = Field(
        default=False,
        description="Whether a user has confirmed this link",
    )

    # Details
    notes: str | None = Field(
        default=None,
        description="User notes about this relationship",
    )
    link_metadata: str | None = Field(
        default=None,
        description="JSON-encoded additional metadata",
    )

    # Linker provenance
    linker_name: str | None = Field(
        default=None,
        description="Name of the linker that created this (temporal, spatial, etc.)",
    )
    linker_version: str | None = Field(
        default=None,
        description="Version of the linker",
    )

    @property
    def is_bidirectional(self) -> bool:
        """Check if this link type is bidirectional."""
        return self.link_type in (
            LinkType.SAME_TIME,
            LinkType.SAME_LOCATION,
            LinkType.NEAR,
            LinkType.RELATED,
            LinkType.SIMILAR,
        )
