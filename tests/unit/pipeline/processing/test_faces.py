"""Unit tests for FaceStage."""

import pytest

# Skip entire module if ML dependencies not installed
pytest.importorskip("facenet_pytorch")

from uuid import uuid4

import torch

from potluck.core.exceptions import ProcessingError
from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import StageStatus
from potluck.pipeline.processing.faces import FaceStage


class TestFaceStage:
    """Tests for FaceStage."""

    def test_stage_has_name(self) -> None:
        """FaceStage should have a NAME attribute."""
        stage = FaceStage()
        assert stage.NAME == "faces"

    def test_should_execute_only_images(self) -> None:
        """FaceStage should only process images."""
        stage = FaceStage()

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

        assert stage.should_execute(image_media) is True
        assert stage.should_execute(video_media) is False

    def test_skip_non_image(self) -> None:
        """FaceStage should skip non-image media."""
        stage = FaceStage()
        media = Media(
            id=uuid4(),
            file_path="/test.mp4",
            media_type=MediaType.VIDEO,
            source_type="generic",
        )

        result = stage.execute(media)

        assert result.status == StageStatus.SKIPPED

    def test_missing_file_fails(self) -> None:
        """FaceStage should fail for missing files."""
        stage = FaceStage()
        media = Media(
            id=uuid4(),
            file_path="/nonexistent/file.jpg",
            media_type=MediaType.IMAGE,
            source_type="generic",
        )

        result = stage.execute(media)

        assert result.status == StageStatus.FAILED
        assert result.error_message is not None
        assert "not found" in result.error_message.lower()

    def test_default_device_selection(self) -> None:
        """FaceStage should auto-select device."""
        stage = FaceStage()
        expected_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        assert stage._device == expected_device

    def test_explicit_device_selection(self) -> None:
        """FaceStage should accept explicit device selection."""
        stage = FaceStage(device="cpu")
        assert stage._device == torch.device("cpu")

    def test_custom_clustering_settings(self) -> None:
        """FaceStage should accept custom clustering settings."""
        stage = FaceStage(
            clustering_eps=0.5,
            min_samples=3,
        )
        assert stage._clustering_eps == 0.5
        assert stage._min_samples == 3


class TestFaceStageClustering:
    """Tests for FaceStage clustering functionality."""

    def test_cluster_embeddings_not_enough_samples(self) -> None:
        """cluster_embeddings should return all as noise with insufficient samples."""
        stage = FaceStage(min_samples=3)
        embeddings = [[0.1] * 512, [0.2] * 512]
        face_ids = [uuid4(), uuid4()]

        clusters = stage.cluster_embeddings(embeddings, face_ids)

        assert -1 in clusters
        assert len(clusters[-1]) == 2

    def test_compute_cluster_centroid(self) -> None:
        """compute_cluster_centroid should return mean of embeddings."""
        stage = FaceStage()
        embeddings = [
            [1.0, 2.0, 3.0],
            [3.0, 4.0, 5.0],
        ]

        centroid = stage.compute_cluster_centroid(embeddings)

        assert len(centroid) == 3
        assert centroid[0] == 2.0
        assert centroid[1] == 3.0
        assert centroid[2] == 4.0

    def test_compute_cluster_centroid_empty(self) -> None:
        """compute_cluster_centroid should raise for empty list."""
        stage = FaceStage()

        with pytest.raises(ProcessingError, match="empty"):
            stage.compute_cluster_centroid([])

    def test_compute_embedding_distance(self) -> None:
        """compute_embedding_distance should return Euclidean distance."""
        stage = FaceStage()
        e1 = [0.0, 0.0, 0.0]
        e2 = [3.0, 4.0, 0.0]

        distance = stage.compute_embedding_distance(e1, e2)

        assert distance == pytest.approx(5.0)

    def test_compute_embedding_distance_identical(self) -> None:
        """compute_embedding_distance should return 0 for identical embeddings."""
        stage = FaceStage()
        e1 = [1.0, 2.0, 3.0]

        distance = stage.compute_embedding_distance(e1, e1)

        assert distance == pytest.approx(0.0)

    def test_find_closest_cluster_empty(self) -> None:
        """find_closest_cluster should return None for empty clusters."""
        stage = FaceStage()
        embedding = [0.5] * 512

        closest_id, distance = stage.find_closest_cluster(embedding, {})

        assert closest_id is None
        assert distance == float("inf")

    def test_find_closest_cluster_within_threshold(self) -> None:
        """find_closest_cluster should return closest cluster within threshold."""
        stage = FaceStage(clustering_eps=1.0)
        embedding = [0.0, 0.0, 0.0]
        cluster_id = uuid4()
        cluster_centroids = {
            cluster_id: [0.1, 0.1, 0.1],
        }

        closest_id, distance = stage.find_closest_cluster(embedding, cluster_centroids)

        assert closest_id == cluster_id
        assert distance < 1.0

    def test_find_closest_cluster_beyond_threshold(self) -> None:
        """find_closest_cluster should return None if beyond threshold."""
        stage = FaceStage(clustering_eps=0.1)
        embedding = [0.0, 0.0, 0.0]
        cluster_id = uuid4()
        cluster_centroids = {
            cluster_id: [10.0, 10.0, 10.0],
        }

        closest_id, distance = stage.find_closest_cluster(embedding, cluster_centroids)

        assert closest_id is None
