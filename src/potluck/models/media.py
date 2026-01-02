"""Media models for photos, videos, and other files."""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlmodel import Field, Relationship, SQLModel

from potluck.models.base import GeolocatedEntity, SimpleEntity, SourceType
from potluck.models.utils import utc_now


class MediaType(str, Enum):
    """Type of media file."""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    OTHER = "other"


class EmbeddingType(str, Enum):
    """Type of embedding for media."""

    CLIP = "clip"  # CLIP visual embedding
    OCR = "ocr"  # OCR text embedding
    CAPTION = "caption"  # Image caption embedding
    AUDIO_TRANSCRIPT = "audio_transcript"  # Audio transcription embedding


class Media(GeolocatedEntity, table=True):
    """Media file with path, metadata, and relationships.

    Follows the "paths only" principle - no blob storage in the database.
    Only stores file paths/URLs and metadata.
    """

    __tablename__ = "media"

    # File information
    file_path: str = Field(
        index=True,
        description="Absolute path to the media file on disk",
    )
    original_filename: str | None = Field(
        default=None,
        description="Original filename from the source",
    )
    file_size: int | None = Field(
        default=None,
        description="File size in bytes",
    )
    mime_type: str | None = Field(
        default=None,
        description="MIME type of the file (e.g., image/jpeg)",
    )
    media_type: MediaType = Field(
        default=MediaType.OTHER,
        description="High-level media type category",
    )

    # Hashes for deduplication and integrity
    file_hash: str | None = Field(
        default=None,
        index=True,
        description="SHA256 hash of file content",
    )
    perceptual_hash: str | None = Field(
        default=None,
        index=True,
        description="Perceptual hash for visual similarity (pHash)",
    )

    # Image/video dimensions
    width: int | None = Field(
        default=None,
        description="Width in pixels",
    )
    height: int | None = Field(
        default=None,
        description="Height in pixels",
    )
    duration_seconds: float | None = Field(
        default=None,
        description="Duration in seconds (for video/audio)",
    )

    # EXIF and metadata
    exif_data: str | None = Field(
        default=None,
        description="JSON-encoded EXIF metadata",
    )
    camera_make: str | None = Field(
        default=None,
        description="Camera manufacturer from EXIF",
    )
    camera_model: str | None = Field(
        default=None,
        description="Camera model from EXIF",
    )

    # Extracted content
    ocr_text: str | None = Field(
        default=None,
        description="OCR-extracted text from the image",
    )
    caption: str | None = Field(
        default=None,
        description="AI-generated image caption",
    )
    transcript: str | None = Field(
        default=None,
        description="Audio/video transcript",
    )

    # Source information
    source_url: str | None = Field(
        default=None,
        description="Original URL if downloaded from web",
    )
    album_name: str | None = Field(
        default=None,
        description="Album or folder name from source",
    )

    # Relationships
    embeddings: list["MediaEmbedding"] = Relationship(back_populates="media")

    @property
    def has_text_content(self) -> bool:
        """Check if this media has any extracted text."""
        return bool(self.ocr_text or self.caption or self.transcript)


class MediaEmbedding(SQLModel, table=True):
    """Embedding vector for a media item.

    Supports multiple embedding types per media item (CLIP, OCR, etc.).
    """

    __tablename__ = "media_embeddings"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique identifier for the embedding",
    )
    media_id: UUID = Field(
        foreign_key="media.id",
        index=True,
        description="The media item this embedding belongs to",
    )
    embedding_type: EmbeddingType = Field(
        description="Type of embedding (CLIP, OCR, etc.)",
    )
    model_name: str = Field(
        description="Name of the model used to generate the embedding",
    )
    embedding: list[float] = Field(
        sa_column=Column(Vector(768)),  # Common dimension, adjust as needed
        description="Embedding vector",
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        description="When the embedding was created",
    )

    # Relationships
    media: Media = Relationship(back_populates="embeddings")


class MediaPersonLink(SimpleEntity, table=True):
    """Face detection and person-media association.

    Stores detected faces in media items with their embeddings and bounding boxes.
    Can be linked to a Person (when identified) or to a FaceCluster (for grouping
    unidentified faces). Supports both automatic face detection and manual tagging.
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
    source_type: SourceType = Field(
        description="How this link was established (e.g., google_takeout, manual, face_detection)",
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
        description="512-dimensional face embedding vector (None for manual tags)",
    )
    bbox_x: int | None = Field(
        default=None,
        description="Bounding box top-left X coordinate",
    )
    bbox_y: int | None = Field(
        default=None,
        description="Bounding box top-left Y coordinate",
    )
    bbox_width: int | None = Field(
        default=None,
        description="Bounding box width in pixels",
    )
    bbox_height: int | None = Field(
        default=None,
        description="Bounding box height in pixels",
    )

    # Relationships (cluster is optional since cluster_id is nullable)
    cluster: "FaceCluster" = Relationship(back_populates="face_links")


# Forward reference for type hints
from potluck.models.people import FaceCluster as FaceCluster  # noqa: E402, F401
