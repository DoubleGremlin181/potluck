"""Unit tests for the processing module."""

import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from potluck.core.exceptions import ProcessingError
from potluck.models.media import Media, MediaType
from potluck.processing.base import (
    BatchProcessingResult,
    ProcessingResult,
    ProcessingStatus,
)
from potluck.processing.faces import FaceProcessor
from potluck.processing.hashing import HashingProcessor, compute_phash_distance
from potluck.processing.metadata import MetadataProcessor
from potluck.processing.ocr import OCRProcessor


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


class TestMetadataProcessor:
    """Tests for MetadataProcessor."""

    @staticmethod
    def _create_test_image() -> Path:
        """Create a temporary test image without EXIF."""
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            img = Image.new("RGB", (100, 100), color="blue")
            img.save(f, "JPEG")
            return Path(f.name)

    def test_processor_has_name(self) -> None:
        """MetadataProcessor should have a NAME attribute."""
        processor = MetadataProcessor()
        assert processor.NAME == "metadata"

    def test_should_process_only_images(self) -> None:
        """MetadataProcessor should only process images."""
        processor = MetadataProcessor()

        image_media = Media(
            id=uuid4(),
            file_path="/test.jpg",
            media_type=MediaType.IMAGE,
            source_type="generic",
        )
        video_media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )
        audio_media = Media(
            id=uuid4(),
            file_path="/test.mp3",
            media_type=MediaType.AUDIO,
            source_type="generic",
        )

        assert processor.should_process(image_media) is True
        assert processor.should_process(video_media) is False
        assert processor.should_process(audio_media) is False

    def test_skip_non_image(self) -> None:
        """MetadataProcessor should skip non-image media."""
        processor = MetadataProcessor()
        media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )

        result = processor.process(media)

        assert result.status == ProcessingStatus.SKIPPED

    def test_missing_file_fails(self) -> None:
        """MetadataProcessor should fail for missing files."""
        processor = MetadataProcessor()
        media = Media(
            id=uuid4(),
            file_path="/nonexistent/file.jpg",
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result = processor.process(media)

        assert result.status == ProcessingStatus.FAILED
        assert result.error_message is not None
        assert "not found" in result.error_message.lower()

    def test_image_without_exif(self) -> None:
        """MetadataProcessor should handle images without EXIF data."""
        sample_image = self._create_test_image()
        processor = MetadataProcessor()
        media = Media(
            id=uuid4(),
            file_path=str(sample_image),
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result = processor.process(media)

        assert result.status == ProcessingStatus.COMPLETED
        assert result.data["has_exif"] is False


class TestOCRProcessor:
    """Tests for OCRProcessor."""

    def test_processor_has_name(self) -> None:
        """OCRProcessor should have a NAME attribute."""
        processor = OCRProcessor()
        assert processor.NAME == "ocr"

    def test_should_process_only_images(self) -> None:
        """OCRProcessor should only process images."""
        processor = OCRProcessor()

        image_media = Media(
            id=uuid4(),
            file_path="/test.jpg",
            media_type=MediaType.IMAGE,
            source_type="generic",
        )
        video_media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )

        assert processor.should_process(image_media) is True
        assert processor.should_process(video_media) is False

    def test_skip_non_image(self) -> None:
        """OCRProcessor should skip non-image media."""
        processor = OCRProcessor()
        media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )

        result = processor.process(media)

        assert result.status == ProcessingStatus.SKIPPED

    def test_missing_file_fails(self) -> None:
        """OCRProcessor should fail for missing files."""
        processor = OCRProcessor()
        media = Media(
            id=uuid4(),
            file_path="/nonexistent/file.jpg",
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result = processor.process(media)

        assert result.status == ProcessingStatus.FAILED
        assert result.error_message is not None
        assert "not found" in result.error_message.lower()

    def test_default_languages(self) -> None:
        """OCRProcessor should default to English."""
        processor = OCRProcessor()
        assert processor._languages == ["en"]

    def test_custom_languages(self) -> None:
        """OCRProcessor should accept custom languages."""
        processor = OCRProcessor(languages=["en", "es", "fr"])
        assert processor._languages == ["en", "es", "fr"]

    def test_gpu_default_enabled(self) -> None:
        """OCRProcessor should enable GPU by default."""
        processor = OCRProcessor()
        assert processor._gpu is True

    def test_gpu_can_be_disabled(self) -> None:
        """OCRProcessor should allow disabling GPU."""
        processor = OCRProcessor(gpu=False)
        assert processor._gpu is False


class TestFaceProcessor:
    """Tests for FaceProcessor."""

    def test_processor_has_name(self) -> None:
        """FaceProcessor should have a NAME attribute."""
        processor = FaceProcessor()
        assert processor.NAME == "faces"

    def test_should_process_only_images(self) -> None:
        """FaceProcessor should only process images."""
        processor = FaceProcessor()

        image_media = Media(
            id=uuid4(),
            file_path="/test.jpg",
            media_type=MediaType.IMAGE,
            source_type="generic",
        )
        video_media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )

        assert processor.should_process(image_media) is True
        assert processor.should_process(video_media) is False

    def test_skip_non_image(self) -> None:
        """FaceProcessor should skip non-image media."""
        processor = FaceProcessor()
        media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )

        result = processor.process(media)

        assert result.status == ProcessingStatus.SKIPPED

    def test_missing_file_fails(self) -> None:
        """FaceProcessor should fail for missing files."""
        processor = FaceProcessor()
        media = Media(
            id=uuid4(),
            file_path="/nonexistent/file.jpg",
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result = processor.process(media)

        assert result.status == ProcessingStatus.FAILED
        assert result.error_message is not None
        assert "not found" in result.error_message.lower()

    def test_default_model_settings(self) -> None:
        """FaceProcessor should use Facenet model by default."""
        processor = FaceProcessor()
        assert processor._model_name == "Facenet"
        assert processor._detector_backend == "retinaface"

    def test_custom_model_settings(self) -> None:
        """FaceProcessor should accept custom model settings."""
        processor = FaceProcessor(
            model_name="VGG-Face",
            detector_backend="mtcnn",
            clustering_eps=0.5,
            min_samples=3,
        )
        assert processor._model_name == "VGG-Face"
        assert processor._detector_backend == "mtcnn"
        assert processor._clustering_eps == 0.5
        assert processor._min_samples == 3


class TestFaceProcessorClustering:
    """Tests for FaceProcessor clustering functionality."""

    def test_cluster_embeddings_not_enough_samples(self) -> None:
        """cluster_embeddings should return all as noise with insufficient samples."""
        processor = FaceProcessor(min_samples=3)
        embeddings = [[0.1] * 128, [0.2] * 128]
        face_ids = [uuid4(), uuid4()]

        clusters = processor.cluster_embeddings(embeddings, face_ids)

        assert -1 in clusters
        assert len(clusters[-1]) == 2

    def test_compute_cluster_centroid(self) -> None:
        """compute_cluster_centroid should return mean of embeddings."""
        processor = FaceProcessor()
        embeddings = [
            [1.0, 2.0, 3.0],
            [3.0, 4.0, 5.0],
        ]

        centroid = processor.compute_cluster_centroid(embeddings)

        assert len(centroid) == 3
        assert centroid[0] == 2.0
        assert centroid[1] == 3.0
        assert centroid[2] == 4.0

    def test_compute_cluster_centroid_empty(self) -> None:
        """compute_cluster_centroid should raise for empty list."""
        processor = FaceProcessor()

        with pytest.raises(ProcessingError, match="empty"):
            processor.compute_cluster_centroid([])

    def test_compute_embedding_distance(self) -> None:
        """compute_embedding_distance should return Euclidean distance."""
        processor = FaceProcessor()
        e1 = [0.0, 0.0, 0.0]
        e2 = [3.0, 4.0, 0.0]

        distance = processor.compute_embedding_distance(e1, e2)

        assert distance == pytest.approx(5.0)

    def test_compute_embedding_distance_identical(self) -> None:
        """compute_embedding_distance should return 0 for identical embeddings."""
        processor = FaceProcessor()
        e1 = [1.0, 2.0, 3.0]

        distance = processor.compute_embedding_distance(e1, e1)

        assert distance == pytest.approx(0.0)

    def test_find_closest_cluster_empty(self) -> None:
        """find_closest_cluster should return None for empty clusters."""
        processor = FaceProcessor()
        embedding = [0.5] * 128

        closest_id, distance = processor.find_closest_cluster(embedding, {})

        assert closest_id is None
        assert distance == float("inf")

    def test_find_closest_cluster_within_threshold(self) -> None:
        """find_closest_cluster should return closest cluster within threshold."""
        processor = FaceProcessor(clustering_eps=1.0)
        embedding = [0.0, 0.0, 0.0]
        cluster_id = uuid4()
        cluster_centroids = {
            cluster_id: [0.1, 0.1, 0.1],
        }

        closest_id, distance = processor.find_closest_cluster(embedding, cluster_centroids)

        assert closest_id == cluster_id
        assert distance < 1.0

    def test_find_closest_cluster_beyond_threshold(self) -> None:
        """find_closest_cluster should return None if beyond threshold."""
        processor = FaceProcessor(clustering_eps=0.1)
        embedding = [0.0, 0.0, 0.0]
        cluster_id = uuid4()
        cluster_centroids = {
            cluster_id: [10.0, 10.0, 10.0],
        }

        closest_id, distance = processor.find_closest_cluster(embedding, cluster_centroids)

        assert closest_id is None
