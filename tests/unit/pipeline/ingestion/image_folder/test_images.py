"""Tests for image/media folder ingester."""

import struct
from pathlib import Path

from potluck.models.base import EntityType, SourceType
from potluck.models.media import Media, MediaType
from potluck.pipeline.ingestion.image_folder import ImageFolderStage
from potluck.pipeline.ingestion.image_folder.images import _get_media_type


def _create_minimal_jpeg(path: Path) -> None:
    """Create a minimal valid JPEG file (SOI + EOI markers)."""
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 10 + b"\xff\xd9")


def _create_minimal_png(path: Path) -> None:
    """Create a minimal valid PNG file (signature + IHDR + IEND)."""
    # PNG signature
    sig = b"\x89PNG\r\n\x1a\n"
    # IHDR chunk: 1x1 8-bit RGB
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr_crc = b"\x00" * 4  # Simplified CRC
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + ihdr_crc
    # IEND chunk
    iend = struct.pack(">I", 0) + b"IEND" + b"\x00" * 4
    path.write_bytes(sig + ihdr + iend)


class TestImageFolderDetection:
    """Tests for ImageFolderStage.detect()."""

    def test_detect_images(self, tmp_path: Path) -> None:
        """Detection counts image files."""
        _create_minimal_jpeg(tmp_path / "photo1.jpg")
        _create_minimal_png(tmp_path / "photo2.png")
        (tmp_path / "readme.txt").write_text("Not a media file")

        stage = ImageFolderStage()
        result = stage.detect(tmp_path)

        assert EntityType.MEDIA in result.entity_counts
        assert result.entity_counts[EntityType.MEDIA] == 2

    def test_detect_mixed_media(self, tmp_path: Path) -> None:
        """Detection counts images, videos, and audio."""
        _create_minimal_jpeg(tmp_path / "photo.jpg")
        (tmp_path / "video.mp4").write_bytes(b"\x00" * 100)
        (tmp_path / "audio.mp3").write_bytes(b"\x00" * 100)

        stage = ImageFolderStage()
        result = stage.detect(tmp_path)

        assert result.entity_counts[EntityType.MEDIA] == 3

    def test_detect_empty_directory(self, tmp_path: Path) -> None:
        """Empty directories return no counts."""
        stage = ImageFolderStage()
        result = stage.detect(tmp_path)
        assert result.entity_counts == {}

    def test_detect_nested(self, tmp_path: Path) -> None:
        """Detection finds files in nested directories."""
        sub_dir = tmp_path / "2024" / "vacation"
        sub_dir.mkdir(parents=True)
        _create_minimal_jpeg(sub_dir / "beach.jpg")
        _create_minimal_jpeg(tmp_path / "selfie.jpg")

        stage = ImageFolderStage()
        result = stage.detect(tmp_path)

        assert result.entity_counts[EntityType.MEDIA] == 2


class TestImageIngestion:
    """Tests for media file ingestion."""

    def test_ingest_images(self, tmp_path: Path) -> None:
        """Images are ingested as Media entities."""
        _create_minimal_jpeg(tmp_path / "photo.jpg")
        _create_minimal_png(tmp_path / "image.png")

        stage = ImageFolderStage()
        entities = list(stage.execute(tmp_path))

        media_entities = [e for e in entities if isinstance(e, Media)]
        assert len(media_entities) == 2
        assert all(m.source_type == SourceType.IMAGE_FOLDER for m in media_entities)

    def test_media_fields(self, tmp_path: Path) -> None:
        """Media entity fields are populated correctly."""
        _create_minimal_jpeg(tmp_path / "photo.jpg")

        stage = ImageFolderStage()
        entities = list(stage.execute(tmp_path))
        media = [e for e in entities if isinstance(e, Media)][0]

        assert media.original_filename == "photo.jpg"
        assert media.media_type == MediaType.IMAGE
        assert media.file_size is not None
        assert media.file_size > 0
        assert media.file_hash is not None
        assert media.content_hash == media.file_hash
        assert media.file_path is not None
        assert media.occurred_at is not None  # Falls back to mtime

    def test_album_from_directory(self, tmp_path: Path) -> None:
        """Album name is inferred from parent directory."""
        album_dir = tmp_path / "Vacation 2024"
        album_dir.mkdir()
        _create_minimal_jpeg(album_dir / "beach.jpg")

        stage = ImageFolderStage()
        entities = list(stage.execute(tmp_path))
        media = [e for e in entities if isinstance(e, Media)][0]

        assert media.album_name == "Vacation 2024"

    def test_nested_album(self, tmp_path: Path) -> None:
        """Nested directories form a path-based album name."""
        nested = tmp_path / "2024" / "Summer"
        nested.mkdir(parents=True)
        _create_minimal_jpeg(nested / "pic.jpg")

        stage = ImageFolderStage()
        entities = list(stage.execute(tmp_path))
        media = [e for e in entities if isinstance(e, Media)][0]

        assert media.album_name == "2024/Summer"

    def test_skip_non_media(self, tmp_path: Path) -> None:
        """Non-media files are not ingested."""
        (tmp_path / "readme.txt").write_text("Not media")
        (tmp_path / "data.csv").write_text("Also not media")
        _create_minimal_jpeg(tmp_path / "photo.jpg")

        stage = ImageFolderStage()
        entities = list(stage.execute(tmp_path))

        assert len(entities) == 1

    def test_file_hash_deduplication(self, tmp_path: Path) -> None:
        """Same content produces same hash for deduplication."""
        content = b"\xff\xd8\xff\xe0" + b"\x00" * 10 + b"\xff\xd9"
        (tmp_path / "copy1.jpg").write_bytes(content)
        (tmp_path / "copy2.jpg").write_bytes(content)

        stage = ImageFolderStage()
        entities = list(stage.execute(tmp_path))
        media = [e for e in entities if isinstance(e, Media)]

        assert len(media) == 2
        assert media[0].file_hash == media[1].file_hash  # Same content, same hash


class TestMediaTypeDetection:
    """Tests for file extension to MediaType mapping."""

    def test_image_types(self) -> None:
        assert _get_media_type(".jpg") == MediaType.IMAGE
        assert _get_media_type(".png") == MediaType.IMAGE
        assert _get_media_type(".heic") == MediaType.IMAGE

    def test_video_types(self) -> None:
        assert _get_media_type(".mp4") == MediaType.VIDEO
        assert _get_media_type(".mov") == MediaType.VIDEO

    def test_audio_types(self) -> None:
        assert _get_media_type(".mp3") == MediaType.AUDIO
        assert _get_media_type(".wav") == MediaType.AUDIO
        assert _get_media_type(".flac") == MediaType.AUDIO

    def test_unknown_type(self) -> None:
        assert _get_media_type(".xyz") == MediaType.OTHER
