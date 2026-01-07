"""Face detection and clustering models.

This module contains models for face detection results and face clustering,
kept together to avoid circular import issues between FaceCluster and
MediaPersonLink which reference each other.
"""

from enum import Enum
from typing import Self
from uuid import UUID

from pgvector.sqlalchemy import Vector
from pydantic import field_validator, model_validator
from sqlalchemy import Column
from sqlmodel import Field, Relationship

from potluck.models.base import SimpleEntity

# Face embeddings are 512-dimensional vectors
FACE_EMBEDDING_DIM = 512


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
        sa_column=Column(Vector(512)),
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
        ge=0,
        description="Number of faces in this cluster",
    )

    # Relationships
    face_links: list["MediaPersonLink"] = Relationship(back_populates="cluster")

    @field_validator("representative_encoding")
    @classmethod
    def validate_embedding_dimension(cls, v: list[float]) -> list[float]:
        """Validate that the embedding has exactly 512 dimensions."""
        if len(v) != FACE_EMBEDDING_DIM:
            raise ValueError(
                f"representative_encoding must have {FACE_EMBEDDING_DIM} dimensions, got {len(v)}"
            )
        return v

    @model_validator(mode="after")
    def validate_status_person_consistency(self) -> Self:
        """Ensure person_id is set when status is CONFIRMED."""
        if self.status == ClusterStatus.CONFIRMED and self.person_id is None:
            raise ValueError("person_id is required when status is CONFIRMED")
        return self


class MediaPersonLink(SimpleEntity, table=True):
    """Face detection and person-media association.

    Stores detected faces in media items with their embeddings and bounding boxes.
    Can be linked to a Person (when identified) or to a FaceCluster (for grouping
    unidentified faces).
    """

    __tablename__ = "media_person_links"

    media_id: UUID = Field(
        foreign_key="media.id",
        index=True,
        description="The media item containing this face",
    )
    person_id: UUID | None = Field(
        default=None,
        foreign_key="people.id",
        index=True,
        description="The person this face belongs to (None if unidentified)",
    )
    cluster_id: UUID | None = Field(
        default=None,
        foreign_key="face_clusters.id",
        index=True,
        description="The cluster this face belongs to (for grouping before identification)",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score for automatic detection",
    )
    is_confirmed: bool = Field(
        default=False,
        description="Whether the identification is user-confirmed",
    )
    # Face embedding and bounding box (for detected faces)
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(512), nullable=True),
        description="512-dimensional face embedding vector",
    )
    bbox_x: int | None = Field(
        default=None,
        ge=0,
        description="Bounding box top-left X coordinate",
    )
    bbox_y: int | None = Field(
        default=None,
        ge=0,
        description="Bounding box top-left Y coordinate",
    )
    bbox_width: int | None = Field(
        default=None,
        ge=1,
        description="Bounding box width in pixels",
    )
    bbox_height: int | None = Field(
        default=None,
        ge=1,
        description="Bounding box height in pixels",
    )

    # Relationships (cluster is optional since cluster_id is nullable)
    cluster: "FaceCluster" = Relationship(back_populates="face_links")

    @field_validator("embedding")
    @classmethod
    def validate_embedding_dimension(cls, v: list[float] | None) -> list[float] | None:
        """Validate that the embedding has exactly 512 dimensions if provided."""
        if v is not None and len(v) != FACE_EMBEDDING_DIM:
            raise ValueError(f"embedding must have {FACE_EMBEDDING_DIM} dimensions, got {len(v)}")
        return v

    @model_validator(mode="after")
    def validate_bbox_atomicity(self) -> Self:
        """Ensure all bounding box fields are set together or all None."""
        bbox_fields = [self.bbox_x, self.bbox_y, self.bbox_width, self.bbox_height]
        non_none_count = sum(1 for f in bbox_fields if f is not None)

        if non_none_count not in (0, 4):
            raise ValueError(
                "Bounding box fields (bbox_x, bbox_y, bbox_width, bbox_height) "
                "must all be set together or all be None"
            )
        return self
