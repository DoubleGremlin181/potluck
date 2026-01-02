"""Face detection and clustering stage using MTCNN + ArcFace (PyTorch native).

Uses MTCNN for detection (from facenet-pytorch) and ArcFace IResNet for recognition.
All inference runs on native PyTorch.

Requires ML dependencies: pip install potluck[ml]
"""

import time
from pathlib import Path
from typing import ClassVar
from uuid import UUID

import numpy as np
import torch
from facenet_pytorch import MTCNN
from PIL import Image
from sklearn.cluster import DBSCAN

from potluck.core.exceptions import ProcessingError
from potluck.core.logging import get_logger
from potluck.models.media import Media, MediaType
from potluck.pipeline.dtos import StageResult, StageStatus
from potluck.pipeline.processing._arcface import download_weights, get_weights_path, iresnet50
from potluck.pipeline.processing.base import BaseProcessingStage

logger = get_logger(__name__)


class FaceStage(BaseProcessingStage):
    """Stage for face detection using MTCNN + ArcFace (PyTorch native).

    Uses MTCNN for face detection and ArcFace IResNet50 for 512-dimensional
    face embeddings. The embeddings can later be clustered to group similar
    faces together using DBSCAN.

    Note: ArcFace models are for non-commercial research purposes only.
    """

    NAME: ClassVar[str] = "faces"

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
        """Initialize the face stage.

        Args:
            device: Device to use for inference ('cuda', 'cpu', or None for auto).
            clustering_eps: DBSCAN eps parameter for clustering.
            min_samples: DBSCAN min_samples parameter.
            confidence_threshold: Minimum confidence for face detection (0.0-1.0).
        """
        self._device = torch.device(
            device if device else ("cuda" if torch.cuda.is_available() else "cpu")
        )
        self._clustering_eps = clustering_eps
        self._min_samples = min_samples
        self._confidence_threshold = confidence_threshold

        # Lazy-load models on first use
        self._mtcnn: MTCNN | None = None
        self._recognizer: torch.nn.Module | None = None

    def _load_models(self) -> None:
        """Load face detection and embedding models."""
        if self._mtcnn is None:
            logger.info(f"Loading MTCNN face detector on {self._device}")
            self._mtcnn = MTCNN(
                keep_all=True,
                device=self._device,
                post_process=False,  # Return raw crops, we'll preprocess for ArcFace
            )

        if self._recognizer is None:
            logger.info(f"Loading ArcFace IResNet50 recognizer on {self._device}")
            self._recognizer = iresnet50(num_features=512)

            # Download weights if not present
            weights_path = get_weights_path()
            if not weights_path.exists():
                logger.info("Downloading ArcFace weights (first time setup)...")
                download_weights()

            if weights_path.exists():
                state_dict = torch.load(weights_path, map_location=self._device, weights_only=True)

                # Handle different checkpoint formats - some have 'arcface.' prefix
                if any(k.startswith("arcface.") for k in state_dict):
                    state_dict = {
                        k.replace("arcface.", ""): v
                        for k, v in state_dict.items()
                        if k.startswith("arcface.")
                    }

                # Try to load, handling potential mismatches
                try:
                    self._recognizer.load_state_dict(state_dict, strict=True)
                    logger.info(f"Loaded ArcFace weights from {weights_path}")
                except RuntimeError as e:
                    logger.warning(f"Could not load weights strictly: {e}")
                    # Try non-strict loading
                    self._recognizer.load_state_dict(state_dict, strict=False)
                    logger.info(f"Loaded ArcFace weights (non-strict) from {weights_path}")
            else:
                logger.warning(
                    f"ArcFace weights not found at {weights_path}, "
                    "using randomly initialized model (embeddings will not be meaningful)"
                )

            self._recognizer.to(self._device)
            self._recognizer.eval()
            self._recognizer.requires_grad_(False)

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
                face_tensor = face_tensor.to(self._device)

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
