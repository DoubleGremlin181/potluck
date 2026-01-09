"""Unit tests for processing Celery tasks.

These tests verify the task logic without requiring a running Celery worker.
For integration tests with actual task execution, see tests/integration/.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from potluck.pipeline.dtos import StageResult, StageStatus


class TestRunHashingProcessor:
    """Tests for run_hashing_processor task."""

    @pytest.mark.ml
    def test_hashing_returns_correct_structure(self) -> None:
        """Task returns dict with expected keys."""
        from potluck.pipeline.tasks.processing import run_hashing_processor

        # Mock the database and stage
        mock_media = MagicMock()
        mock_media.id = uuid4()
        mock_media.file_path = "/test/image.jpg"

        mock_result = StageResult(
            item_id=mock_media.id,
            stage_name="hashing",
            status=StageStatus.COMPLETED,
            processing_time_ms=100,
            data={"file_hash": "abc123", "perceptual_hash": "def456"},
        )

        with (
            patch("potluck.pipeline.tasks.processing.get_engine"),
            patch("potluck.pipeline.tasks.processing.Session"),
            patch("potluck.pipeline.tasks.processing._get_media", return_value=mock_media),
            patch("potluck.pipeline.tasks.processing._update_media_fields"),
            patch(
                "potluck.pipeline.processing.hashing.HashingProcessor.execute",
                return_value=mock_result,
            ),
        ):
            result = run_hashing_processor(str(mock_media.id))

            assert "media_id" in result
            assert "status" in result
            assert result["status"] == "completed"
            assert "file_hash" in result
            assert "perceptual_hash" in result

    @pytest.mark.ml
    def test_hashing_rejects_missing_media(self) -> None:
        """Task rejects when media not found."""
        from celery.exceptions import Reject

        from potluck.pipeline.tasks.processing import run_hashing_processor

        with (
            patch("potluck.pipeline.tasks.processing.get_engine"),
            patch("potluck.pipeline.tasks.processing.Session"),
            patch("potluck.pipeline.tasks.processing._get_media", return_value=None),
        ):
            with pytest.raises(Reject) as exc_info:
                run_hashing_processor(str(uuid4()))

            assert "Media not found" in str(exc_info.value)


class TestRunMetadataProcessor:
    """Tests for run_metadata_processor task."""

    @pytest.mark.ml
    def test_metadata_returns_correct_structure(self) -> None:
        """Task returns dict with expected keys."""
        from potluck.pipeline.tasks.processing import run_metadata_processor

        mock_media = MagicMock()
        mock_media.id = uuid4()
        mock_media.file_path = "/test/image.jpg"

        mock_result = StageResult(
            item_id=mock_media.id,
            stage_name="metadata",
            status=StageStatus.COMPLETED,
            processing_time_ms=50,
            data={
                "has_exif": True,
                "latitude": 37.7749,
                "longitude": -122.4194,
            },
        )

        with (
            patch("potluck.pipeline.tasks.processing.get_engine"),
            patch("potluck.pipeline.tasks.processing.Session"),
            patch("potluck.pipeline.tasks.processing._get_media", return_value=mock_media),
            patch("potluck.pipeline.tasks.processing._update_media_fields"),
            patch(
                "potluck.pipeline.processing.metadata.MetadataProcessor.execute",
                return_value=mock_result,
            ),
        ):
            result = run_metadata_processor(str(mock_media.id))

            assert "media_id" in result
            assert "status" in result
            assert "has_exif" in result


class TestRunFacesProcessor:
    """Tests for run_faces_processor task."""

    @pytest.mark.ml
    def test_faces_persists_detected_faces(self) -> None:
        """Task persists detected faces to MediaPersonLink."""
        from potluck.pipeline.tasks.processing import run_faces_processor

        mock_media = MagicMock()
        mock_media.id = uuid4()
        mock_media.file_path = "/test/image.jpg"

        mock_result = StageResult(
            item_id=mock_media.id,
            stage_name="faces",
            status=StageStatus.COMPLETED,
            processing_time_ms=500,
            data={
                "faces": [
                    {
                        "embedding": [0.1] * 512,
                        "bbox_x": 10,
                        "bbox_y": 20,
                        "bbox_width": 100,
                        "bbox_height": 120,
                        "confidence": 0.95,
                    },
                    {
                        "embedding": [0.2] * 512,
                        "bbox_x": 200,
                        "bbox_y": 50,
                        "bbox_width": 80,
                        "bbox_height": 100,
                        "confidence": 0.88,
                    },
                ],
            },
        )

        mock_session = MagicMock()

        with (
            patch("potluck.pipeline.tasks.processing.get_engine"),
            patch("potluck.pipeline.tasks.processing.Session", return_value=mock_session),
            patch("potluck.pipeline.tasks.processing._get_media", return_value=mock_media),
            patch(
                "potluck.pipeline.processing.faces.FaceProcessor.execute",
                return_value=mock_result,
            ),
        ):
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)

            result = run_faces_processor(str(mock_media.id))

            assert result["faces_detected"] == 2
            # Verify session.add was called for each face
            assert mock_session.add.call_count >= 2


class TestRunProcessingPipeline:
    """Tests for run_processing_pipeline orchestration."""

    @pytest.mark.ml
    def test_pipeline_chains_tasks_correctly(self) -> None:
        """Pipeline chains all processing tasks in correct order."""
        from potluck.pipeline.tasks.processing import run_processing_pipeline

        media_id = str(uuid4())

        with patch("potluck.pipeline.tasks.processing.chain") as mock_chain:
            mock_chain.return_value.apply_async = MagicMock()

            run_processing_pipeline(media_id)

            # Verify chain was called
            mock_chain.assert_called_once()

            # Get the signatures passed to chain
            call_args = mock_chain.call_args[0]
            assert len(call_args) == 5  # 5 tasks in pipeline

    @pytest.mark.ml
    def test_pipeline_uses_immutable_signatures(self) -> None:
        """Pipeline uses .si() for immutable signatures."""
        from potluck.pipeline.tasks.processing import (
            run_captioning_processor,
            run_faces_processor,
            run_hashing_processor,
            run_metadata_processor,
            run_ocr_processor,
        )

        media_id = str(uuid4())

        # Verify each task has .si() method (immutable signature)
        for task in [
            run_hashing_processor,
            run_metadata_processor,
            run_ocr_processor,
            run_faces_processor,
            run_captioning_processor,
        ]:
            sig = task.si(media_id)
            assert sig.immutable is True


class TestClusterUnassignedFaces:
    """Tests for cluster_unassigned_faces task."""

    @pytest.mark.ml
    def test_clustering_skips_when_no_faces(self) -> None:
        """Task returns early when no unclustered faces exist."""
        from potluck.pipeline.tasks.processing import cluster_unassigned_faces

        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        with (
            patch("potluck.pipeline.tasks.processing.get_engine"),
            patch("potluck.pipeline.tasks.processing.Session", return_value=mock_session),
        ):
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session.execute.return_value = mock_result

            result = cluster_unassigned_faces()

            assert result["status"] == "completed"
            assert result["faces_processed"] == 0
            assert result["clusters_created"] == 0
