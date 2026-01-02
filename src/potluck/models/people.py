"""People models for identity aggregation across data sources."""

from datetime import date, datetime
from enum import Enum
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlmodel import Field, Relationship, SQLModel

from potluck.models.base import SimpleEntity, SourceType
from potluck.models.utils import utc_now


class AliasType(str, Enum):
    """Type of person alias identifier."""

    NAME = "name"
    EMAIL = "email"
    PHONE = "phone"
    USERNAME = "username"
    SOCIAL_HANDLE = "social_handle"


class Person(SQLModel, table=True):
    """Main entity that aggregates identities across sources.

    A Person represents a single real-world individual, potentially
    linked to multiple aliases (names, emails, phones) and face encodings.
    """

    __tablename__ = "people"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the person",
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        description="When the person was created in the database",
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column_kwargs={"onupdate": utc_now},
        description="When the person was last updated",
    )
    display_name: str = Field(
        description="Primary display name for this person",
    )
    photo_url: str | None = Field(
        default=None,
        description="URL to the person's profile photo",
    )
    date_of_birth: date | None = Field(
        default=None,
        description="Date of birth if known",
    )
    notes: str | None = Field(
        default=None,
        description="User notes about this person",
    )
    is_self: bool = Field(
        default=False,
        description="Whether this person is the data owner",
    )
    merged_into_id: UUID | None = Field(
        default=None,
        foreign_key="people.id",
        description="If merged, points to the canonical Person record",
    )

    # Relationships
    aliases: list["PersonAlias"] = Relationship(back_populates="person")
    face_encodings: list["FaceEncoding"] = Relationship(back_populates="person")

    @property
    def is_merged(self) -> bool:
        """Check if this person has been merged into another."""
        return self.merged_into_id is not None


class PersonAlias(SQLModel, table=True):
    """Alias (name, email, phone, etc.) linked to a Person.

    Tracks different identifiers for the same person across sources.
    """

    __tablename__ = "person_aliases"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the alias",
    )
    person_id: UUID = Field(
        foreign_key="people.id",
        index=True,
        description="The person this alias belongs to",
    )
    alias_type: AliasType = Field(
        description="Type of alias (name, email, phone, etc.)",
    )
    value: str = Field(
        index=True,
        description="The alias value (e.g., email address, phone number)",
    )
    normalized_value: str | None = Field(
        default=None,
        index=True,
        description="Normalized/canonical form of the value for matching",
    )
    source_type: SourceType = Field(
        description="Source where this alias was discovered",
    )
    is_primary: bool = Field(
        default=False,
        description="Whether this is the primary alias of its type",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score for this alias association (0.0-1.0)",
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        description="When the alias was created",
    )

    # Relationships
    person: Person = Relationship(back_populates="aliases")


class FaceEncoding(SQLModel, table=True):
    """Reference face embedding vector for a Person.

    Stores face recognition embeddings used to identify a person in photos.
    These are reference vectors - when processing media, detected faces are
    compared against these embeddings to identify who appears in the image.

    The person-media association is stored in MediaPersonLink, not here.
    """

    __tablename__ = "face_encodings"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the face encoding",
    )
    person_id: UUID = Field(
        foreign_key="people.id",
        index=True,
        description="The person this face belongs to",
    )
    embedding: list[float] = Field(
        sa_column=Column(Vector(128)),  # FaceNet uses 128-d vectors
        description="128-dimensional face embedding vector",
    )
    source_media_id: UUID | None = Field(
        default=None,
        index=True,
        description="Optional: media item this encoding was extracted from (for provenance)",
    )
    is_primary: bool = Field(
        default=False,
        description="Whether this is the primary reference encoding for the person",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Quality/confidence score for this encoding",
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        description="When the face encoding was created",
    )

    # Relationships
    person: Person = Relationship(back_populates="face_encodings")


class ClusterStatus(str, Enum):
    """Status of a face cluster."""

    PENDING = "pending"  # Awaiting user review
    CONFIRMED = "confirmed"  # Assigned to a Person
    REJECTED = "rejected"  # User marked as not a real face/garbage


class FaceCluster(SimpleEntity, table=True):
    """Cluster of similar faces before Person assignment.

    Groups detected faces by visual similarity using DBSCAN clustering.
    Users can:
    - Assign a cluster to an existing Person
    - Create a new Person from a cluster
    - Mark a cluster for review (low confidence)
    - Reject garbage clusters (false positives)
    """

    __tablename__ = "face_clusters"

    representative_encoding: list[float] = Field(
        sa_column=Column(Vector(128)),
        description="Centroid/representative face embedding for the cluster",
    )
    status: ClusterStatus = Field(
        default=ClusterStatus.PENDING,
        description="Current status of the cluster",
    )
    person_id: UUID | None = Field(
        default=None,
        foreign_key="people.id",
        index=True,
        description="Person this cluster was assigned to (when confirmed)",
    )
    needs_review: bool = Field(
        default=False,
        description="Flag for low-confidence matches needing user review",
    )
    face_count: int = Field(
        default=0,
        description="Number of faces in this cluster",
    )

    # Relationships
    face_links: list["MediaPersonLink"] = Relationship(back_populates="cluster")


# Forward reference for type hints
from potluck.models.media import MediaPersonLink as MediaPersonLink  # noqa: E402, F401
