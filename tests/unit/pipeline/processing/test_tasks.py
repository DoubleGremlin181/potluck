"""Unit tests for processing Celery tasks.

These tests verify the task logic without requiring a running Celery worker.
For integration tests with actual task execution, see tests/integration/.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from potluck.models.base import EntityType


class TestRunBatchEntityPipeline:
    """Tests for run_batch_entity_pipeline orchestration."""

    @pytest.mark.ml
    def test_batch_pipeline_chains_batch_tasks(self) -> None:
        """Batch pipeline chains batch tasks in correct order."""
        from potluck.pipeline.tasks.processing import run_batch_entity_pipeline

        entity_ids = [str(uuid4()) for _ in range(5)]

        with patch("potluck.pipeline.tasks.processing.chain") as mock_chain:
            mock_chain.return_value.apply_async = MagicMock()

            run_batch_entity_pipeline(EntityType.MEDIA.value, entity_ids)

            mock_chain.assert_called_once()
            call_args = mock_chain.call_args[0]
            # Media pipeline: hashing, metadata, ocr, faces, media_embedding, captioning
            assert len(call_args) == 6

    @pytest.mark.ml
    def test_batch_pipeline_skips_empty_ids(self) -> None:
        """Batch pipeline does nothing for empty entity list."""
        from potluck.pipeline.tasks.processing import run_batch_entity_pipeline

        with patch("potluck.pipeline.tasks.processing.chain") as mock_chain:
            run_batch_entity_pipeline(EntityType.MEDIA.value, [])

            mock_chain.assert_not_called()

    @pytest.mark.ml
    def test_batch_pipeline_skips_unknown_entity_type(self) -> None:
        """Batch pipeline handles entity types with no registered batch processors."""
        from potluck.pipeline.tasks.processing import run_batch_entity_pipeline

        # Entity type with no processors registered should not raise
        with patch("potluck.pipeline.tasks.processing.chain") as mock_chain:
            # Use a type that might not have batch tasks registered
            run_batch_entity_pipeline("calendar_event", [str(uuid4())])
            mock_chain.assert_not_called()

    @pytest.mark.ml
    def test_entity_pipeline_wraps_batch(self) -> None:
        """run_entity_pipeline should call run_batch_entity_pipeline with [id]."""
        from potluck.pipeline.tasks.processing import run_entity_pipeline

        entity_id = str(uuid4())

        with patch("potluck.pipeline.tasks.processing.run_batch_entity_pipeline") as mock_batch:
            run_entity_pipeline(EntityType.MEDIA.value, entity_id)

            mock_batch.assert_called_once_with(EntityType.MEDIA.value, [entity_id])


class TestRunBatchStageTask:
    """Tests for run_batch_stage_task infrastructure."""

    @pytest.mark.ml
    def test_batch_stage_skips_empty_needs_processing(self) -> None:
        """Batch stage should skip when needs_processing is empty."""
        from potluck.pipeline.processing.core.base import run_batch_stage_task
        from potluck.pipeline.processing.processors.hashing import HashingProcessor

        mock_task = MagicMock()
        previous_result = {"entity_type": "media", "needs_processing": []}

        result = run_batch_stage_task(
            mock_task, previous_result, EntityType.MEDIA, HashingProcessor
        )

        assert result["needs_processing"] == []
        assert result["total"] == 0

    @pytest.mark.ml
    def test_batch_stage_propagates_needs_processing(self) -> None:
        """Batch stage should propagate needs_processing to next stage."""
        from potluck.pipeline.processing.core.base import run_batch_stage_task
        from potluck.pipeline.processing.processors.hashing import HashingProcessor

        mock_task = MagicMock()
        entity_ids = [str(uuid4()), str(uuid4())]
        previous_result = {"entity_type": "media", "needs_processing": entity_ids}

        mock_media1 = MagicMock()
        mock_media1.id = entity_ids[0]
        mock_media2 = MagicMock()
        mock_media2.id = entity_ids[1]

        with (
            patch("potluck.pipeline.processing.core.base.get_engine"),
            patch("potluck.pipeline.processing.core.base.Session") as mock_session_cls,
            patch(
                "potluck.pipeline.processing.core.base._get_entities_bulk",
                return_value=({entity_ids[0]: mock_media1, entity_ids[1]: mock_media2}, []),
            ),
            patch.object(HashingProcessor, "execute_batch") as mock_execute_batch,
        ):
            mock_session = MagicMock()
            mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

            from potluck.pipeline.dtos import BatchStageResult

            mock_execute_batch.return_value = BatchStageResult(
                stage_name="hashing",
                total=2,
                completed=2,
                failed=0,
                skipped=0,
                results=[],
            )

            result = run_batch_stage_task(
                mock_task, previous_result, EntityType.MEDIA, HashingProcessor
            )

            assert result["needs_processing"] == entity_ids
            assert result["completed"] == 2


class TestProcessorRegistryBatch:
    """Tests for batch task registration in ProcessorRegistry."""

    @pytest.mark.ml
    def test_batch_pipeline_returns_only_processors_with_batch_tasks(self) -> None:
        """get_batch_pipeline should only return processors with batch_task_func."""
        from potluck.pipeline.processing.core.registry import ProcessorRegistry

        pipeline = ProcessorRegistry.get_batch_pipeline(EntityType.MEDIA)

        # All configs in batch pipeline should have batch_task_func set
        for config in pipeline:
            assert config.batch_task_func is not None

    @pytest.mark.ml
    def test_batch_pipeline_sorted_by_priority(self) -> None:
        """get_batch_pipeline should return processors sorted by priority."""
        from potluck.pipeline.processing.core.registry import ProcessorRegistry

        pipeline = ProcessorRegistry.get_batch_pipeline(EntityType.MEDIA)

        priorities = [config.priority for config in pipeline]
        assert priorities == sorted(priorities)


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
            patch("potluck.pipeline.processing.processors.clustering.get_engine"),
            patch(
                "potluck.pipeline.processing.processors.clustering.Session",
                return_value=mock_session,
            ),
        ):
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session.execute.return_value = mock_result

            result = cluster_unassigned_faces()

            assert result["status"] == "completed"
            assert result["faces_processed"] == 0
            assert result["clusters_created"] == 0
