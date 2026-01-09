"""Tests for face detection and clustering models."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from potluck.models.faces import (
    ClusterStatus,
    FaceCluster,
    MediaPersonLink,
)


class TestFaceCluster:
    """Tests for FaceCluster model validators."""

    def test_valid_pending_cluster(self) -> None:
        """Pending cluster without person_id is valid."""
        cluster = FaceCluster(
            representative_encoding=[0.1] * 512,
            status=ClusterStatus.PENDING,
        )
        assert cluster.status == ClusterStatus.PENDING
        assert cluster.person_id is None

    def test_valid_confirmed_cluster(self) -> None:
        """Confirmed cluster with person_id is valid."""
        cluster = FaceCluster(
            representative_encoding=[0.1] * 512,
            status=ClusterStatus.CONFIRMED,
            person_id=uuid4(),
        )
        assert cluster.status == ClusterStatus.CONFIRMED

    def test_confirmed_without_person_raises(self) -> None:
        """Confirmed status without person_id raises validation error via model_validate."""
        # Note: SQLModel table models may bypass Pydantic validators during __init__
        # Use model_validate to ensure validation runs
        with pytest.raises(ValidationError) as exc_info:
            FaceCluster.model_validate(
                {
                    "representative_encoding": [0.1] * 512,
                    "status": ClusterStatus.CONFIRMED,
                }
            )
        assert "person_id is required when status is CONFIRMED" in str(exc_info.value)

    def test_rejected_cluster_without_person(self) -> None:
        """Rejected cluster without person_id is valid."""
        cluster = FaceCluster(
            representative_encoding=[0.1] * 512,
            status=ClusterStatus.REJECTED,
        )
        assert cluster.status == ClusterStatus.REJECTED

    def test_embedding_wrong_dimension_raises(self) -> None:
        """Embedding with wrong dimensions raises validation error via model_validate."""
        with pytest.raises(ValidationError) as exc_info:
            FaceCluster.model_validate(
                {
                    "representative_encoding": [0.1] * 256,  # Wrong: should be 512
                    "status": ClusterStatus.PENDING,
                }
            )
        assert "must have 512 dimensions" in str(exc_info.value)

    def test_face_count_must_be_non_negative(self) -> None:
        """Face count cannot be negative via model_validate."""
        with pytest.raises(ValidationError) as exc_info:
            FaceCluster.model_validate(
                {
                    "representative_encoding": [0.1] * 512,
                    "face_count": -1,
                }
            )
        assert "greater than or equal to 0" in str(exc_info.value)


class TestMediaPersonLink:
    """Tests for MediaPersonLink model validators."""

    def test_valid_link_with_bbox(self) -> None:
        """Link with all bounding box fields is valid."""
        link = MediaPersonLink(
            media_id=uuid4(),
            embedding=[0.1] * 512,
            bbox_x=10,
            bbox_y=20,
            bbox_width=100,
            bbox_height=120,
        )
        assert link.bbox_x == 10
        assert link.bbox_width == 100

    def test_valid_link_without_bbox(self) -> None:
        """Link without bounding box is valid."""
        link = MediaPersonLink(
            media_id=uuid4(),
            embedding=[0.1] * 512,
        )
        assert link.bbox_x is None
        assert link.bbox_width is None

    def test_partial_bbox_raises(self) -> None:
        """Partial bounding box raises validation error via model_validate."""
        with pytest.raises(ValidationError) as exc_info:
            MediaPersonLink.model_validate(
                {
                    "media_id": uuid4(),
                    "bbox_x": 10,
                    "bbox_y": 20,
                    # Missing bbox_width and bbox_height
                }
            )
        assert "must all be set together or all be None" in str(exc_info.value)

    def test_embedding_wrong_dimension_raises(self) -> None:
        """Embedding with wrong dimensions raises validation error via model_validate."""
        with pytest.raises(ValidationError) as exc_info:
            MediaPersonLink.model_validate(
                {
                    "media_id": uuid4(),
                    "embedding": [0.1] * 256,  # Wrong: should be 512
                }
            )
        assert "must have 512 dimensions" in str(exc_info.value)

    def test_embedding_none_is_valid(self) -> None:
        """Link without embedding is valid."""
        link = MediaPersonLink(
            media_id=uuid4(),
        )
        assert link.embedding is None

    def test_confidence_bounds(self) -> None:
        """Confidence must be between 0 and 1 via model_validate."""
        # Valid confidence
        link = MediaPersonLink.model_validate(
            {
                "media_id": uuid4(),
                "confidence": 0.95,
            }
        )
        assert link.confidence == 0.95

        # Invalid: too high
        with pytest.raises(ValidationError):
            MediaPersonLink.model_validate(
                {
                    "media_id": uuid4(),
                    "confidence": 1.5,
                }
            )

        # Invalid: negative
        with pytest.raises(ValidationError):
            MediaPersonLink.model_validate(
                {
                    "media_id": uuid4(),
                    "confidence": -0.1,
                }
            )

    def test_bbox_coordinates_must_be_non_negative(self) -> None:
        """Bounding box x/y coordinates cannot be negative via model_validate."""
        with pytest.raises(ValidationError):
            MediaPersonLink.model_validate(
                {
                    "media_id": uuid4(),
                    "bbox_x": -10,
                    "bbox_y": 20,
                    "bbox_width": 100,
                    "bbox_height": 120,
                }
            )

    def test_bbox_dimensions_must_be_positive(self) -> None:
        """Bounding box width/height must be at least 1 via model_validate."""
        with pytest.raises(ValidationError):
            MediaPersonLink.model_validate(
                {
                    "media_id": uuid4(),
                    "bbox_x": 10,
                    "bbox_y": 20,
                    "bbox_width": 0,  # Must be >= 1
                    "bbox_height": 120,
                }
            )
