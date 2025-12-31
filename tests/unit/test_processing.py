"""Unit tests for the processing module."""

import tempfile
from pathlib import Path
from uuid import uuid4

from PIL import Image

from potluck.core.exceptions import ProcessingError
from potluck.models.media import Media, MediaType
from potluck.processing.base import (
    BatchProcessingResult,
    ProcessingResult,
    ProcessingStatus,
)
from potluck.processing.hashing import HashingProcessor, compute_phash_distance


class TestProcessingStatus:
    """Tests for ProcessingStatus enum."""

    def test_all_statuses_defined(self) -> None:
        """All expected processing statuses should be defined."""
        assert ProcessingStatus.PENDING.value == "pending"
        assert ProcessingStatus.RUNNING.value == "running"
        assert ProcessingStatus.COMPLETED.value == "completed"
        assert ProcessingStatus.FAILED.value == "failed"
        assert ProcessingStatus.SKIPPED.value == "skipped"


class TestProcessingResult:
    """Tests for ProcessingResult DTO."""

    def test_result_creation(self) -> None:
        """ProcessingResult should be created with required fields."""
        media_id = uuid4()
        result = ProcessingResult(
            media_id=media_id,
            processor_name="test",
            status=ProcessingStatus.COMPLETED,
        )

        assert result.media_id == media_id
        assert result.processor_name == "test"
        assert result.status == ProcessingStatus.COMPLETED
        assert result.error_message is None
        assert result.processing_time_ms == 0
        assert result.data == {}

    def test_result_with_error(self) -> None:
        """ProcessingResult should store error messages."""
        result = ProcessingResult(
            media_id=uuid4(),
            processor_name="test",
            status=ProcessingStatus.FAILED,
            error_message="Something went wrong",
        )

        assert result.status == ProcessingStatus.FAILED
        assert result.error_message == "Something went wrong"

    def test_result_with_data(self) -> None:
        """ProcessingResult should store extracted data."""
        result = ProcessingResult(
            media_id=uuid4(),
            processor_name="hashing",
            status=ProcessingStatus.COMPLETED,
            data={"file_hash": "abc123", "perceptual_hash": "def456"},
        )

        assert result.data["file_hash"] == "abc123"
        assert result.data["perceptual_hash"] == "def456"


class TestBatchProcessingResult:
    """Tests for BatchProcessingResult DTO."""

    def test_batch_result_creation(self) -> None:
        """BatchProcessingResult should aggregate individual results."""
        results = [
            ProcessingResult(
                media_id=uuid4(),
                processor_name="test",
                status=ProcessingStatus.COMPLETED,
            ),
            ProcessingResult(
                media_id=uuid4(),
                processor_name="test",
                status=ProcessingStatus.FAILED,
                error_message="Error",
            ),
            ProcessingResult(
                media_id=uuid4(),
                processor_name="test",
                status=ProcessingStatus.SKIPPED,
            ),
        ]

        batch = BatchProcessingResult(
            processor_name="test",
            total=3,
            completed=1,
            failed=1,
            skipped=1,
            results=results,
        )

        assert batch.total == 3
        assert batch.completed == 1
        assert batch.failed == 1
        assert batch.skipped == 1
        assert len(batch.results) == 3


class TestProcessingError:
    """Tests for ProcessingError exception."""

    def test_processing_error_creation(self) -> None:
        """ProcessingError should be created with message."""
        error = ProcessingError("Something went wrong")
        assert str(error) == "Something went wrong"
        assert error.message == "Something went wrong"

    def test_processing_error_inheritance(self) -> None:
        """ProcessingError should inherit from PotluckError."""
        from potluck.core.exceptions import PotluckError

        error = ProcessingError("Test error")
        assert isinstance(error, PotluckError)
        assert isinstance(error, Exception)


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

    def test_processor_has_name(self) -> None:
        """HashingProcessor should have a NAME attribute."""
        processor = HashingProcessor()
        assert processor.NAME == "hashing"

    def test_hash_image_computes_both_hashes(self) -> None:
        """HashingProcessor should compute both SHA256 and pHash for images."""
        sample_image = self._create_test_image()
        processor = HashingProcessor()
        media = Media(
            id=uuid4(),
            file_path=str(sample_image),
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result = processor.process(media)

        assert result.status == ProcessingStatus.COMPLETED
        assert result.data["file_hash"] is not None
        assert len(result.data["file_hash"]) == 64  # SHA256 hex length
        assert result.data["perceptual_hash"] is not None

    def test_hash_non_image_only_file_hash(self) -> None:
        """HashingProcessor should only compute SHA256 for non-images."""
        sample_text_file = self._create_test_text_file()
        processor = HashingProcessor()
        media = Media(
            id=uuid4(),
            file_path=str(sample_text_file),
            media_type=MediaType.DOCUMENT,
            source_type="generic",
        )

        result = processor.process(media)

        assert result.status == ProcessingStatus.COMPLETED
        assert result.data["file_hash"] is not None
        assert result.data["perceptual_hash"] is None

    def test_hash_missing_file_fails(self) -> None:
        """HashingProcessor should fail for missing files."""
        processor = HashingProcessor()
        media = Media(
            id=uuid4(),
            file_path="/nonexistent/file.png",
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result = processor.process(media)

        assert result.status == ProcessingStatus.FAILED
        assert result.error_message is not None
        assert "not found" in result.error_message.lower()

    def test_hash_deterministic(self) -> None:
        """HashingProcessor should produce deterministic hashes."""
        sample_image = self._create_test_image()
        processor = HashingProcessor()
        media = Media(
            id=uuid4(),
            file_path=str(sample_image),
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result1 = processor.process(media)
        result2 = processor.process(media)

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
