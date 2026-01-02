"""Tests for Media and MediaEmbedding models."""

from uuid import UUID, uuid4

from potluck.models.base import SourceType
from potluck.models.media import EmbeddingType, Media, MediaEmbedding, MediaPersonLink, MediaType


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
