"""Unit tests for HashingProcessor."""

import tempfile
from pathlib import Path
from uuid import uuid4

from PIL import Image

from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import StageStatus
from potluck.pipeline.processing.processors.hashing import HashingProcessor, compute_phash_distance


class TestHashingProcessor:
    """Tests for HashingProcessor."""

    @staticmethod
    def _create_test_image() -> Path:
        """Create a temporary test image."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img = Image.new("RGB", (100, 100), color="red")
            img.save(f, "PNG")
            return Path(f.name)

    @staticmethod
    def _create_test_text_file() -> Path:
        """Create a temporary text file."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"Hello, World!")
            return Path(f.name)

    def test_stage_has_name(self) -> None:
        """HashingProcessor should have a NAME attribute."""
        stage = HashingProcessor()
        assert stage.NAME == "hashing"

    def test_hash_image_computes_both_hashes(self) -> None:
        """HashingProcessor should compute both SHA256 and pHash for images."""
        sample_image = self._create_test_image()
        stage = HashingProcessor()
        media = Media(
            id=uuid4(),
            file_path=str(sample_image),
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result = stage.execute(media)

        assert result.status == StageStatus.COMPLETED
        assert result.data["file_hash"] is not None
        assert len(result.data["file_hash"]) == 64  # SHA256 hex length
        assert result.data["perceptual_hash"] is not None

    def test_hash_non_image_only_file_hash(self) -> None:
        """HashingProcessor should only compute SHA256 for non-images."""
        sample_text_file = self._create_test_text_file()
        stage = HashingProcessor()
        media = Media(
            id=uuid4(),
            file_path=str(sample_text_file),
            media_type=MediaType.DOCUMENT,
            source_type="generic",
        )

        result = stage.execute(media)

        assert result.status == StageStatus.COMPLETED
        assert result.data["file_hash"] is not None
        assert result.data["perceptual_hash"] is None

    def test_hash_missing_file_fails(self) -> None:
        """HashingProcessor should fail for missing files."""
        stage = HashingProcessor()
        media = Media(
            id=uuid4(),
            file_path="/nonexistent/file.png",
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result = stage.execute(media)

        assert result.status == StageStatus.FAILED
        assert result.error_message is not None
        assert "not found" in result.error_message.lower()

    def test_hash_deterministic(self) -> None:
        """HashingProcessor should produce deterministic hashes."""
        sample_image = self._create_test_image()
        stage = HashingProcessor()
        media = Media(
            id=uuid4(),
            file_path=str(sample_image),
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result1 = stage.execute(media)
        result2 = stage.execute(media)

        assert result1.data["file_hash"] == result2.data["file_hash"]
        assert result1.data["perceptual_hash"] == result2.data["perceptual_hash"]


class TestPerceptualHashDistance:
    """Tests for perceptual hash distance computation."""

    def test_identical_hashes_zero_distance(self) -> None:
        """Identical hashes should have zero distance."""
        hash_val = "0123456789abcdef"
        assert compute_phash_distance(hash_val, hash_val) == 0

    def test_different_hashes_positive_distance(self) -> None:
        """Different hashes should have positive distance."""
        hash1 = "0000000000000000"
        hash2 = "ffffffffffffffff"
        distance = compute_phash_distance(hash1, hash2)
        assert distance > 0
