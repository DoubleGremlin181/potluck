"""Unit tests for processing Celery tasks.

These tests verify the task logic without requiring a running Celery worker.
For integration tests with actual task execution, see tests/integration/.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from potluck.processing.base import ProcessingResult, ProcessingStatus


class TestProcessMediaHashing:
    """Tests for process_media_hashing task."""

    @pytest.mark.ml
    def test_hashing_returns_correct_structure(self) -> None:
        """Task returns dict with expected keys."""
        from potluck.processing.tasks import process_media_hashing

        # Mock the database and processor
        mock_media = MagicMock()
        mock_media.id = uuid4()
        mock_media.file_path = "/test/image.jpg"

        mock_result = ProcessingResult(
            media_id=mock_media.id,
            processor_name="hashing",
            status=ProcessingStatus.COMPLETED,
            processing_time_ms=100,
            data={"file_hash": "abc123", "perceptual_hash": "def456"},
        )

        with (
            patch("potluck.processing.tasks.get_engine"),
            patch("potluck.processing.tasks.Session"),
            patch("potluck.processing.tasks._get_media", return_value=mock_media),
            patch("potluck.processing.tasks._update_media_fields"),
            patch(
                "potluck.processing.hashing.HashingProcessor.process",
                return_value=mock_result,
            ),
        ):
            result = process_media_hashing(str(mock_media.id))

            assert "media_id" in result
            assert "status" in result
            assert result["status"] == "completed"
            assert "file_hash" in result
            assert "perceptual_hash" in result

    @pytest.mark.ml
    def test_hashing_rejects_missing_media(self) -> None:
        """Task rejects when media not found."""
        from celery.exceptions import Reject

        from potluck.processing.tasks import process_media_hashing

        with (
            patch("potluck.processing.tasks.get_engine"),
            patch("potluck.processing.tasks.Session"),
            patch("potluck.processing.tasks._get_media", return_value=None),
        ):
            with pytest.raises(Reject) as exc_info:
                process_media_hashing(str(uuid4()))

            assert "Media not found" in str(exc_info.value)


class TestProcessMediaMetadata:
    """Tests for process_media_metadata task."""

    @pytest.mark.ml
    def test_metadata_returns_correct_structure(self) -> None:
        """Task returns dict with expected keys."""
        from potluck.processing.tasks import process_media_metadata

        mock_media = MagicMock()
        mock_media.id = uuid4()
        mock_media.file_path = "/test/image.jpg"

        mock_result = ProcessingResult(
            media_id=mock_media.id,
            processor_name="metadata",
            status=ProcessingStatus.COMPLETED,
            processing_time_ms=50,
            data={
                "has_exif": True,
                "latitude": 37.7749,
                "longitude": -122.4194,
            },
        )

        with (
            patch("potluck.processing.tasks.get_engine"),
            patch("potluck.processing.tasks.Session"),
            patch("potluck.processing.tasks._get_media", return_value=mock_media),
            patch("potluck.processing.tasks._update_media_fields"),
            patch(
                "potluck.processing.metadata.MetadataProcessor.process",
                return_value=mock_result,
            ),
        ):
            result = process_media_metadata(str(mock_media.id))

            assert "media_id" in result
            assert "status" in result
            assert "has_exif" in result


class TestProcessMediaFaces:
    """Tests for process_media_faces task."""

    @pytest.mark.ml
    def test_faces_persists_detected_faces(self) -> None:
        """Task persists detected faces to MediaPersonLink."""
        from potluck.processing.tasks import process_media_faces

        mock_media = MagicMock()
        mock_media.id = uuid4()
        mock_media.file_path = "/test/image.jpg"

        mock_result = ProcessingResult(
            media_id=mock_media.id,
            processor_name="faces",
            status=ProcessingStatus.COMPLETED,
            processing_time_ms=500,
            data={
                "faces": [
                    {
                        "embedding": [0.1] * 128,
                        "bbox_x": 10,
                        "bbox_y": 20,
                        "bbox_width": 100,
                        "bbox_height": 120,
                        "confidence": 0.95,
                    },
                    {
                        "embedding": [0.2] * 128,
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
            patch("potluck.processing.tasks.get_engine"),
            patch("potluck.processing.tasks.Session", return_value=mock_session),
            patch("potluck.processing.tasks._get_media", return_value=mock_media),
            patch(
                "potluck.processing.faces.FaceProcessor.process",
                return_value=mock_result,
            ),
        ):
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)

            result = process_media_faces(str(mock_media.id))

            assert result["faces_detected"] == 2
            # Verify session.add was called for each face
            assert mock_session.add.call_count >= 2


class TestProcessMediaPipeline:
    """Tests for process_media_pipeline orchestration."""

    @pytest.mark.ml
    def test_pipeline_chains_tasks_correctly(self) -> None:
        """Pipeline chains all processing tasks in correct order."""
        from potluck.processing.tasks import process_media_pipeline

        media_id = str(uuid4())

        with patch("potluck.processing.tasks.chain") as mock_chain:
            mock_chain.return_value.apply_async = MagicMock()

            process_media_pipeline(media_id)

            # Verify chain was called
            mock_chain.assert_called_once()

            # Get the signatures passed to chain
            call_args = mock_chain.call_args[0]
            assert len(call_args) == 5  # 5 tasks in pipeline

    @pytest.mark.ml
    def test_pipeline_uses_immutable_signatures(self) -> None:
        """Pipeline uses .si() for immutable signatures."""
        from potluck.processing.tasks import (
            process_media_caption,
            process_media_faces,
            process_media_hashing,
            process_media_metadata,
            process_media_ocr,
        )

        media_id = str(uuid4())

        # Verify each task has .si() method (immutable signature)
        for task in [
            process_media_hashing,
            process_media_metadata,
            process_media_ocr,
            process_media_faces,
            process_media_caption,
        ]:
            sig = task.si(media_id)
            assert sig.immutable is True


class TestClusterUnassignedFaces:
    """Tests for cluster_unassigned_faces task."""

    @pytest.mark.ml
    def test_clustering_skips_when_no_faces(self) -> None:
        """Task returns early when no unclustered faces exist."""
        from potluck.processing.tasks import cluster_unassigned_faces

        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        with (
            patch("potluck.processing.tasks.get_engine"),
            patch("potluck.processing.tasks.Session", return_value=mock_session),
        ):
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session.execute.return_value = mock_result

            result = cluster_unassigned_faces()

            assert result["status"] == "completed"
            assert result["faces_processed"] == 0
            assert result["clusters_created"] == 0
