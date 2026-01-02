"""Face detection and clustering stage using DeepFace.

Requires ML dependencies: pip install potluck[ml]
"""

import time
from pathlib import Path
from typing import ClassVar
from uuid import UUID

import numpy as np
from deepface import DeepFace
from sklearn.cluster import DBSCAN

from potluck.core.exceptions import ProcessingError
from potluck.core.logging import get_logger
from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import StageResult, StageStatus
from potluck.pipeline.processing.base import BaseProcessingStage

logger = get_logger(__name__)


class FaceStage(BaseProcessingStage):
    """Stage for face detection using DeepFace with FaceNet backend.

    Detects faces in images and generates 128-dimensional embedding vectors
    for each face. The embeddings can later be clustered to group similar
    faces together using DBSCAN.
    """

    NAME: ClassVar[str] = "faces"

    # DBSCAN clustering parameters
    DEFAULT_CLUSTERING_EPS = 0.6
    DEFAULT_MIN_SAMPLES = 2

    def __init__(
        self,
        *,
        model_name: str = "Facenet",
        detector_backend: str = "retinaface",
        clustering_eps: float = DEFAULT_CLUSTERING_EPS,
        min_samples: int = DEFAULT_MIN_SAMPLES,
    ) -> None:
        """Initialize the face stage.

        Args:
            model_name: DeepFace model for embeddings (Facenet, VGG-Face, etc.)
            detector_backend: Face detector (retinaface, mtcnn, opencv, etc.)
            clustering_eps: DBSCAN eps parameter for clustering
            min_samples: DBSCAN min_samples parameter
        """
        self._model_name = model_name
        self._detector_backend = detector_backend
        self._clustering_eps = clustering_eps
        self._min_samples = min_samples

    def should_execute(self, media: Media) -> bool:
        """Only process images."""
        return media.media_type == MediaType.IMAGE

    def execute(self, media: Media) -> StageResult:
        """Detect faces in the media and extract embeddings.

        Args:
            media: The media item to process.

        Returns:
            StageResult with face data including embeddings and bounding boxes.
        """
        start_time = time.monotonic()

        if not self.should_execute(media):
            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.SKIPPED,
            )

        file_path = Path(media.file_path)
        if not file_path.exists():
            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.FAILED,
                error_message=f"File not found: {media.file_path}",
            )

        try:
            results = DeepFace.represent(
                img_path=str(file_path),
                model_name=self._model_name,
                detector_backend=self._detector_backend,
                enforce_detection=False,
            )

            faces = []
            for result in results:
                embedding = result.get("embedding", [])
                facial_area = result.get("facial_area", {})
                confidence = result.get("face_confidence", 1.0)

                if embedding and len(embedding) == 128:
                    faces.append(
                        {
                            "embedding": embedding,
                            "bbox_x": facial_area.get("x", 0),
                            "bbox_y": facial_area.get("y", 0),
                            "bbox_width": facial_area.get("w", 0),
                            "bbox_height": facial_area.get("h", 0),
                            "confidence": confidence if confidence is not None else 1.0,
                        }
                    )
                elif embedding:
                    logger.warning(
                        f"Skipping face with invalid embedding dimension: "
                        f"expected 128, got {len(embedding)}"
                    )

            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.COMPLETED,
                processing_time_ms=elapsed_ms,
                data={
                    "faces": faces,
                    "face_count": len(faces),
                    "model_name": self._model_name,
                    "detector_backend": self._detector_backend,
                },
            )

        except Exception as e:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            logger.exception(f"Face detection failed for {media.file_path}: {e}")
            return StageResult(
                item_id=media.id,
                stage_name=self.NAME,
                status=StageStatus.FAILED,
                processing_time_ms=elapsed_ms,
                error_message=f"Face detection failed: {e}",
            )

    def cluster_embeddings(
        self,
        embeddings: list[list[float]],
        face_ids: list[UUID],
    ) -> dict[int, list[UUID]]:
        """Cluster face embeddings using DBSCAN.

        Args:
            embeddings: List of 128-dimensional face embedding vectors.
            face_ids: Corresponding UUIDs for each embedding.

        Returns:
            Dict mapping cluster label to list of face UUIDs.
            Cluster -1 contains unclustered (noise) faces.
        """
        if len(embeddings) < self._min_samples:
            return {-1: face_ids}

        embedding_matrix = np.array(embeddings)

        clustering = DBSCAN(
            eps=self._clustering_eps,
            min_samples=self._min_samples,
            metric="euclidean",
        ).fit(embedding_matrix)

        clusters: dict[int, list[UUID]] = {}
        for idx, label in enumerate(clustering.labels_):
            label_int = int(label)
            if label_int not in clusters:
                clusters[label_int] = []
            clusters[label_int].append(face_ids[idx])

        return clusters

    def compute_cluster_centroid(self, embeddings: list[list[float]]) -> list[float]:
        """Compute the centroid (mean) of a set of embeddings."""
        if not embeddings:
            raise ProcessingError("Cannot compute centroid of empty embedding list")

        embedding_matrix = np.array(embeddings)
        centroid = np.mean(embedding_matrix, axis=0)
        result: list[float] = centroid.tolist()
        return result

    def compute_embedding_distance(
        self,
        embedding1: list[float],
        embedding2: list[float],
    ) -> float:
        """Compute the Euclidean distance between two embeddings."""
        v1 = np.array(embedding1)
        v2 = np.array(embedding2)
        return float(np.linalg.norm(v1 - v2))

    def find_closest_cluster(
        self,
        embedding: list[float],
        cluster_centroids: dict[UUID, list[float]],
        threshold: float | None = None,
    ) -> tuple[UUID | None, float]:
        """Find the closest cluster to a given embedding.

        Args:
            embedding: The face embedding to match.
            cluster_centroids: Dict mapping cluster IDs to their centroid embeddings.
            threshold: Maximum distance to consider a match.

        Returns:
            Tuple of (closest cluster UUID or None, distance to closest cluster).
        """
        if threshold is None:
            threshold = self._clustering_eps

        if not cluster_centroids:
            return None, float("inf")

        closest_id: UUID | None = None
        closest_distance = float("inf")

        for cluster_id, centroid in cluster_centroids.items():
            distance = self.compute_embedding_distance(embedding, centroid)
            if distance < closest_distance:
                closest_distance = distance
                closest_id = cluster_id

        if closest_distance > threshold:
            return None, closest_distance

        return closest_id, closest_distance
