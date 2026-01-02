"""Tests for processing with real images.

These tests use actual image files to verify processors work correctly
with real data. They require ML dependencies and should run in Docker.
"""

from pathlib import Path
from uuid import uuid4

import pytest

from potluck.models.base import SourceType
from potluck.models.media import Media, MediaType
from potluck.processing.base import ProcessingStatus


def _create_test_media(file_path: Path) -> Media:
    """Create a Media object for testing without database persistence."""
    return Media(
        id=uuid4(),
        file_path=str(file_path),
        media_type=MediaType.IMAGE,
        source_type=SourceType.GENERIC,
    )


class TestHashingWithRealImages:
    """Test hashing processor with actual image files."""

    @pytest.mark.ml
    def test_hashing_jpeg_image(self, sample_jpeg_path: Path) -> None:
        """HashingProcessor correctly processes JPEG images."""
        from potluck.processing.hashing import HashingProcessor

        media = _create_test_media(sample_jpeg_path)
        processor = HashingProcessor()

        result = processor.process(media)

        assert result.status == ProcessingStatus.COMPLETED
        assert result.data["file_hash"] is not None
        assert len(result.data["file_hash"]) == 64  # SHA256 hex
        assert result.data["perceptual_hash"] is not None

    @pytest.mark.ml
    def test_hashing_png_image(self, sample_png_path: Path) -> None:
        """HashingProcessor correctly processes PNG images."""
        from potluck.processing.hashing import HashingProcessor

        media = _create_test_media(sample_png_path)
        processor = HashingProcessor()

        result = processor.process(media)

        assert result.status == ProcessingStatus.COMPLETED
        assert result.data["file_hash"] is not None
        assert result.data["perceptual_hash"] is not None


class TestPerceptualHashDuplicateDetection:
    """Test perceptual hash detects similar images across formats."""

    @pytest.mark.ml
    def test_identical_images_have_similar_phash(
        self,
        identical_images_different_formats: tuple[Path, Path],
    ) -> None:
        """Same image in PNG and JPEG has similar perceptual hash."""
        from potluck.processing.hashing import HashingProcessor, compute_phash_distance

        jpeg_path, png_path = identical_images_different_formats

        processor = HashingProcessor()

        jpeg_media = _create_test_media(jpeg_path)
        png_media = _create_test_media(png_path)

        jpeg_result = processor.process(jpeg_media)
        png_result = processor.process(png_media)

        assert jpeg_result.status == ProcessingStatus.COMPLETED
        assert png_result.status == ProcessingStatus.COMPLETED

        jpeg_phash = jpeg_result.data["perceptual_hash"]
        png_phash = png_result.data["perceptual_hash"]

        # File hashes should be different (different formats/compression)
        assert jpeg_result.data["file_hash"] != png_result.data["file_hash"]

        # Perceptual hashes should be similar (low distance)
        distance = compute_phash_distance(jpeg_phash, png_phash)
        assert distance < 10, f"Perceptual hash distance {distance} too high for identical images"

    @pytest.mark.ml
    def test_different_images_have_different_phash(
        self,
        sample_jpeg_path: Path,
        image_with_text: Path,
    ) -> None:
        """Different images have different perceptual hashes."""
        from potluck.processing.hashing import HashingProcessor, compute_phash_distance

        processor = HashingProcessor()

        img1_media = _create_test_media(sample_jpeg_path)
        img2_media = _create_test_media(image_with_text)

        result1 = processor.process(img1_media)
        result2 = processor.process(img2_media)

        assert result1.status == ProcessingStatus.COMPLETED
        assert result2.status == ProcessingStatus.COMPLETED

        phash1 = result1.data["perceptual_hash"]
        phash2 = result2.data["perceptual_hash"]

        # Different images should have higher distance
        distance = compute_phash_distance(phash1, phash2)
        assert distance > 5, f"Perceptual hash distance {distance} too low for different images"


class TestOCRWithRealImages:
    """Test OCR processor with actual images containing text."""

    @pytest.mark.ml
    def test_ocr_extracts_text_from_image(self, image_with_text: Path) -> None:
        """OCRProcessor extracts readable text from images."""
        from potluck.processing.ocr import OCRProcessor

        media = _create_test_media(image_with_text)
        processor = OCRProcessor()

        result = processor.process(media)

        assert result.status == ProcessingStatus.COMPLETED
        ocr_text = result.data.get("ocr_text", "")
        # Should find some text (case-insensitive check)
        assert "hello" in ocr_text.lower() or "world" in ocr_text.lower()


class TestFaceDetectionWithRealImages:
    """Test face detection with images."""

    @pytest.mark.ml
    def test_face_detection_returns_embeddings(self, image_with_face: Path) -> None:
        """FaceProcessor returns face embeddings when faces are detected."""
        from potluck.processing.faces import FaceProcessor

        media = _create_test_media(image_with_face)
        processor = FaceProcessor()

        result = processor.process(media)

        # Note: Simple drawn "face" may not be detected by ML model
        # This test verifies the processor runs without error
        assert result.status in (ProcessingStatus.COMPLETED, ProcessingStatus.SKIPPED)

        if result.status == ProcessingStatus.COMPLETED:
            faces = result.data.get("faces", [])
            for face in faces:
                assert "embedding" in face
                assert len(face["embedding"]) == 128  # FaceNet dimension
                assert "bbox_x" in face
                assert "bbox_y" in face


class TestCaptioningWithRealImages:
    """Test image captioning with actual images."""

    @pytest.mark.ml
    def test_captioning_generates_description(self, sample_jpeg_path: Path) -> None:
        """CaptioningProcessor generates text description for images."""
        from potluck.processing.captioning import CaptioningProcessor

        media = _create_test_media(sample_jpeg_path)
        processor = CaptioningProcessor()

        result = processor.process(media)

        assert result.status == ProcessingStatus.COMPLETED
        caption = result.data.get("caption", "")
        assert len(caption) > 0, "Caption should not be empty"


class TestMetadataWithRealImages:
    """Test metadata extraction with actual images."""

    @pytest.mark.ml
    def test_metadata_processes_without_exif(self, sample_jpeg_path: Path) -> None:
        """MetadataProcessor handles images without EXIF gracefully."""
        from potluck.processing.metadata import MetadataProcessor

        media = _create_test_media(sample_jpeg_path)
        processor = MetadataProcessor()

        result = processor.process(media)

        # Generated images don't have EXIF, but processor should complete
        assert result.status == ProcessingStatus.COMPLETED
        # has_exif should be False for generated images
        assert result.data.get("has_exif") is False or result.data.get("exif_data") is None
