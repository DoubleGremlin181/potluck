"""Face detection and clustering processor using MTCNN + ArcFace (PyTorch native).

Uses MTCNN for detection (from facenet-pytorch) and ArcFace IResNet for recognition.
All inference runs on native PyTorch. Uses MLModels for centralized model loading.
"""

import time
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

import numpy as np
import torch
from celery import Task
from celery.exceptions import Retry
from facenet_pytorch import MTCNN
from PIL import Image
from sklearn.cluster import DBSCAN
from sqlmodel import Session, SQLModel

from potluck.core.celery import (
    MAX_RETRIES,
    RETRY_BACKOFF,
    RETRY_BACKOFF_MAX,
    celery_app,
)
from potluck.core.exceptions import ProcessingError
from potluck.core.logging import get_logger
from potluck.models.base import EntityType
from potluck.models.faces import MediaPersonLink
from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import StageResult, StageStatus
from potluck.pipeline.processing.core.base import (
    BaseProcessor,
    run_batch_stage_task,
)
from potluck.pipeline.processing.core.ml import MLModels
from potluck.pipeline.processing.core.registry import ProcessorRegistry

logger = get_logger(__name__)


@ProcessorRegistry.register(priority=40)
class FaceProcessor(BaseProcessor):
    """Processor for face detection using MTCNN + ArcFace (PyTorch native).

    Uses MTCNN for face detection and ArcFace IResNet50 for 512-dimensional
    face embeddings. The embeddings can later be clustered to group similar
    faces together using DBSCAN.

    Uses MLModels for centralized model loading and GPU configuration.
    Note: ArcFace models are for non-commercial research purposes only.
    """

    NAME: ClassVar[str] = "faces"
    SUPPORTED_ENTITY_TYPES: ClassVar[set[EntityType]] = {EntityType.MEDIA}
    # FaceProcessor does NOT use PERSIST_FIELDS - it overrides persist_result()
    # to create MediaPersonLink records instead of updating Media fields

    # DBSCAN clustering parameters
    DEFAULT_CLUSTERING_EPS = 0.6
    DEFAULT_MIN_SAMPLES = 2

    # Detection confidence threshold
    DEFAULT_CONFIDENCE_THRESHOLD = 0.9

    def __init__(
        self,
        *,
        device: str | None = None,
        clustering_eps: float = DEFAULT_CLUSTERING_EPS,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        """Initialize the face processor.

        Args:
            device: Device to use for inference ('cuda', 'cpu', or None for auto based on GPU env var).
            clustering_eps: DBSCAN eps parameter for clustering.
            min_samples: DBSCAN min_samples parameter.
            confidence_threshold: Minimum confidence for face detection (0.0-1.0).
        """
        self._models = MLModels(device=device)
        self._clustering_eps = clustering_eps
        self._min_samples = min_samples
        self._confidence_threshold = confidence_threshold

        # Lazy-load models on first use
        self._mtcnn: MTCNN | None = None
        self._recognizer: torch.nn.Module | None = None

    def _load_models(self) -> None:
        """Load face detection and embedding models from MLModels."""
        if self._mtcnn is None:
            self._mtcnn = self._models.get_face_detector()

        if self._recognizer is None:
            self._recognizer = self._models.get_face_encoder()

    def _preprocess_face_for_arcface(self, face_crop: torch.Tensor) -> torch.Tensor:
        """Preprocess MTCNN face crop for ArcFace recognition.

        MTCNN returns 160x160 RGB tensors (not normalized).
        ArcFace expects 112x112 RGB tensors normalized to [-1, 1].

        Args:
            face_crop: MTCNN face crop tensor of shape (3, 160, 160).

        Returns:
            Preprocessed tensor of shape (1, 3, 112, 112).
        """
        # Resize from 160x160 to 112x112
        face_resized = torch.nn.functional.interpolate(
            face_crop.unsqueeze(0),
            size=(112, 112),
            mode="bilinear",
            align_corners=False,
        )

        # Normalize to [-1, 1] (ArcFace expects this)
        # MTCNN returns values in [0, 255] when post_process=False
        face_normalized = face_resized.div(255).sub(0.5).div(0.5)

        return face_normalized

    def should_execute(self, entity: SQLModel) -> bool:
        """Only process images."""
        media: Media = entity  # type: ignore[assignment]
        return media.media_type == MediaType.IMAGE

    def execute(self, entity: SQLModel) -> StageResult:
        """Detect faces in the media and extract embeddings.

        Args:
            entity: The media entity to process.

        Returns:
            StageResult with face data including embeddings and bounding boxes.
        """
        media: Media = entity  # type: ignore[assignment]
        start_time = time.monotonic()

        if not self.should_execute(entity):
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
            # Load models on first use
            self._load_models()
            assert self._mtcnn is not None
            assert self._recognizer is not None

            # Load and convert image
            img = Image.open(file_path).convert("RGB")

            # Detect faces and get bounding boxes
            boxes, probs = self._mtcnn.detect(img)

            if boxes is None or len(boxes) == 0:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                return StageResult(
                    item_id=media.id,
                    stage_name=self.NAME,
                    status=StageStatus.COMPLETED,
                    processing_time_ms=elapsed_ms,
                    data={
                        "faces": [],
                        "face_count": 0,
                    },
                )

            # Get face crops from MTCNN (returns tensor of shape (N, 3, 160, 160))
            faces_cropped = self._mtcnn(img)

            if faces_cropped is None:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                return StageResult(
                    item_id=media.id,
                    stage_name=self.NAME,
                    status=StageStatus.COMPLETED,
                    processing_time_ms=elapsed_ms,
                    data={
                        "faces": [],
                        "face_count": 0,
                    },
                )

            # Process each detected face
            faces = []
            for box, prob, face_crop in zip(boxes, probs, faces_cropped, strict=False):
                if prob < self._confidence_threshold:
                    continue

                # Preprocess for ArcFace (resize to 112x112, normalize)
                face_tensor = self._preprocess_face_for_arcface(face_crop)
                face_tensor = face_tensor.to(self._models.device)

                # Generate embedding with ArcFace
                with torch.no_grad():
                    embedding = self._recognizer(face_tensor)
                    embedding_list = embedding.cpu().numpy().flatten().tolist()

                if len(embedding_list) == 512:
                    faces.append(
                        {
                            "embedding": embedding_list,
                            "bbox_x": int(box[0]),
                            "bbox_y": int(box[1]),
                            "bbox_width": int(box[2] - box[0]),
                            "bbox_height": int(box[3] - box[1]),
                            "confidence": float(prob),
                        }
                    )
                else:
                    logger.warning(
                        f"Skipping face with invalid embedding dimension: "
                        f"expected 512, got {len(embedding_list)}"
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

    def persist_result(
        self,
        session: Session,
        entity_type: EntityType,
        entity_id: str,
        result: StageResult,
    ) -> dict[str, Any]:
        """Persist detected faces to MediaPersonLink records.

        Unlike other processors that update Media fields, FaceProcessor creates
        new MediaPersonLink records for each detected face.

        Args:
            session: Database session for persistence.
            entity_type: The type of entity being processed.
            entity_id: ID of the entity being processed.
            result: The StageResult from execute().

        Returns:
            Dict with task result summary.
        """
        faces = result.data.get("faces", [])

        for face_data in faces:
            face_link = MediaPersonLink(
                media_id=UUID(entity_id),
                person_id=None,
                cluster_id=None,
                confidence=face_data.get("confidence", 1.0),
                is_confirmed=False,
                embedding=face_data.get("embedding"),
                bbox_x=face_data.get("bbox_x"),
                bbox_y=face_data.get("bbox_y"),
                bbox_width=face_data.get("bbox_width"),
                bbox_height=face_data.get("bbox_height"),
            )
            session.add(face_link)

        if faces:
            session.commit()
            logger.info(f"Persisted {len(faces)} faces for {entity_type.value} {entity_id}")

        return {
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "status": result.status.value,
            "faces_detected": len(faces),
            "processing_time_ms": result.processing_time_ms,
        }

    def cluster_embeddings(
        self,
        embeddings: list[list[float]],
        face_ids: list[UUID],
    ) -> dict[int, list[UUID]]:
        """Cluster face embeddings using DBSCAN.

        Args:
            embeddings: List of 512-dimensional face embedding vectors.
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


# -----------------------------------------------------------------------------
# Celery Task
# -----------------------------------------------------------------------------


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    queue="pipeline",
    autoretry_for=(Retry,),
    retry_backoff=RETRY_BACKOFF,
    retry_backoff_max=RETRY_BACKOFF_MAX,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_faces_batch(
    self: "Task[..., dict[str, Any]]",
    previous_result: dict[str, Any],
    entity_type: str,
) -> dict[str, Any]:
    """Detect faces in a batch of entities (pipeline stage)."""
    return run_batch_stage_task(self, previous_result, EntityType(entity_type), FaceProcessor)


ProcessorRegistry.set_batch_task(FaceProcessor.NAME, run_faces_batch)
