"""Face detection and clustering processor using DeepFace."""

import time
from pathlib import Path
from uuid import UUID

import numpy as np
from deepface import DeepFace
from sklearn.cluster import DBSCAN

from potluck.core.exceptions import ProcessingError
from potluck.core.logging import get_logger
from potluck.models.media import Media, MediaType
from potluck.processing.base import BaseProcessor, ProcessingResult, ProcessingStatus

logger = get_logger(__name__)


class FaceProcessor(BaseProcessor):
    """Processor for face detection using DeepFace with FaceNet backend.

    Detects faces in images and generates 128-dimensional embedding vectors
    for each face. The embeddings can later be clustered to group similar
    faces together using DBSCAN.
    """

    NAME = "faces"

    # DBSCAN clustering parameters
    DEFAULT_CLUSTERING_EPS = 0.6  # Maximum distance between samples in a cluster
    DEFAULT_MIN_SAMPLES = 2  # Minimum samples to form a cluster

    def __init__(
        self,
        *,
        model_name: str = "Facenet",
        detector_backend: str = "retinaface",
        clustering_eps: float = DEFAULT_CLUSTERING_EPS,
        min_samples: int = DEFAULT_MIN_SAMPLES,
    ) -> None:
        """Initialize the face processor.

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

    def should_process(self, media: Media) -> bool:
        """Check if this media should be processed for face detection.

        Only processes images - videos would need frame extraction first.

        Args:
            media: The media item to check.

        Returns:
            True if the media is an image.
        """
        return media.media_type == MediaType.IMAGE

    def process(self, media: Media) -> ProcessingResult:
        """Detect faces in the media and extract embeddings.

        Args:
            media: The media item to process.

        Returns:
            ProcessingResult with face data including embeddings and bounding boxes.
        """
        start_time = time.monotonic()

        if not self.should_process(media):
            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.SKIPPED,
            )

        file_path = Path(media.file_path)
        if not file_path.exists():
            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.FAILED,
                error_message=f"File not found: {media.file_path}",
            )

        try:
            # DeepFace.represent returns a list of dicts with embeddings and facial_area
            results = DeepFace.represent(
                img_path=str(file_path),
                model_name=self._model_name,
                detector_backend=self._detector_backend,
                enforce_detection=False,  # Don't fail if no faces found
            )

            faces = []
            for result in results:
                embedding = result.get("embedding", [])
                facial_area = result.get("facial_area", {})
                confidence = result.get("face_confidence", 1.0)

                # Only include faces with valid embeddings
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

            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.COMPLETED,
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
            return ProcessingResult(
                media_id=media.id,
                processor_name=self.NAME,
                status=ProcessingStatus.FAILED,
                processing_time_ms=elapsed_ms,
                error_message=f"Face detection failed: {e}",
            )

    def cluster_embeddings(
        self,
        embeddings: list[list[float]],
        face_ids: list[UUID],
    ) -> dict[int, list[UUID]]:
        """Cluster face embeddings using DBSCAN.

        Groups similar face embeddings together. Faces that don't fit into
        any cluster are assigned to cluster -1 (noise).

        Args:
            embeddings: List of 128-dimensional face embedding vectors.
            face_ids: Corresponding UUIDs for each embedding.

        Returns:
            Dict mapping cluster label to list of face UUIDs in that cluster.
            Cluster -1 contains unclustered (noise) faces.
        """
        if len(embeddings) < self._min_samples:
            # Not enough faces to form clusters
            return {-1: face_ids}

        # Convert to numpy array for DBSCAN
        embedding_matrix = np.array(embeddings)

        # Run DBSCAN clustering
        clustering = DBSCAN(
            eps=self._clustering_eps,
            min_samples=self._min_samples,
            metric="euclidean",
        ).fit(embedding_matrix)

        # Group face IDs by cluster label
        clusters: dict[int, list[UUID]] = {}
        for idx, label in enumerate(clustering.labels_):
            label_int = int(label)
            if label_int not in clusters:
                clusters[label_int] = []
            clusters[label_int].append(face_ids[idx])

        return clusters

    def compute_cluster_centroid(self, embeddings: list[list[float]]) -> list[float]:
        """Compute the centroid (mean) of a set of embeddings.

        The centroid serves as the representative embedding for a cluster.

        Args:
            embeddings: List of embedding vectors in the cluster.

        Returns:
            The centroid embedding vector.
        """
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
        """Compute the Euclidean distance between two embeddings.

        Lower distance means more similar faces.

        Args:
            embedding1: First embedding vector.
            embedding2: Second embedding vector.

        Returns:
            Euclidean distance between the embeddings.
        """
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
            threshold: Maximum distance to consider a match. Defaults to clustering_eps.

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
