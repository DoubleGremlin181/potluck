"""Tests for ImportSource, ImportRun, and ProcessingProgress models."""

from uuid import UUID, uuid4

from potluck.models.base import EntityType, SourceType
from potluck.models.sources import (
    ImportRun,
    ImportSource,
    ImportStatus,
    ProcessingProgress,
    StageType,
)


class TestImportSourceModels:
    """Tests for ImportSource and ImportRun models."""

    def test_import_source_creation(self) -> None:
        """ImportSource can be created with required fields."""
        source = ImportSource(
            source_type=SourceType.GOOGLE_TAKEOUT,
            name="My Google Takeout",
        )
        assert isinstance(source.id, UUID)
        assert source.source_type == SourceType.GOOGLE_TAKEOUT
        assert source.name == "My Google Takeout"
        assert source.is_active is True
        assert source.description is None
        assert source.config is None

    def test_import_source_optional_fields(self) -> None:
        """ImportSource optional fields can be set."""
        source = ImportSource(
            source_type=SourceType.REDDIT,
            name="Reddit Export",
            description="My Reddit data",
            config='{"username": "test"}',
            is_active=False,
        )
        assert source.description == "My Reddit data"
        assert source.config == '{"username": "test"}'
        assert source.is_active is False

    def test_import_run_creation(self) -> None:
        """ImportRun can be created with required fields."""
        source_id = uuid4()
        run = ImportRun(source_id=source_id)
        assert isinstance(run.id, UUID)
        assert run.source_id == source_id
        assert run.status == ImportStatus.PENDING
        assert run.entities_found == 0
        assert run.entities_created == 0

    def test_import_run_status_enum(self) -> None:
        """ImportStatus enum has expected values."""
        expected = {"pending", "running", "completed", "failed", "cancelled"}
        actual = {s.value for s in ImportStatus}
        assert actual == expected

    def test_import_run_is_running_property(self) -> None:
        """is_running property returns correct value."""
        run = ImportRun(source_id=uuid4(), status=ImportStatus.RUNNING)
        assert run.is_running is True

        run.status = ImportStatus.COMPLETED
        assert run.is_running is False

    def test_import_run_is_finished_property(self) -> None:
        """is_finished property returns correct value."""
        run = ImportRun(source_id=uuid4(), status=ImportStatus.PENDING)
        assert run.is_finished is False

        for status in [ImportStatus.COMPLETED, ImportStatus.FAILED, ImportStatus.CANCELLED]:
            run.status = status
            assert run.is_finished is True

    def test_import_run_progress_percent(self) -> None:
        """progress_percent calculates correctly."""
        run = ImportRun(source_id=uuid4(), progress_current=50, progress_total=100)
        assert run.progress_percent == 50.0

        run.progress_total = None
        assert run.progress_percent is None

        run.progress_total = 0
        assert run.progress_percent is None


class TestStageType:
    """Tests for StageType enum."""

    def test_stage_type_values(self) -> None:
        """StageType should have processor and linker values."""
        assert StageType.PROCESSOR.value == "processor"
        assert StageType.LINKER.value == "linker"
        assert len(StageType) == 2


class TestProcessingProgress:
    """Tests for ProcessingProgress model."""

    def test_creation_with_defaults(self) -> None:
        """ProcessingProgress can be created with required fields."""
        run_id = uuid4()
        progress = ProcessingProgress(
            import_run_id=run_id,
            stage_name="hashing",
            stage_type=StageType.PROCESSOR,
            entity_type=EntityType.MEDIA,
        )
        assert isinstance(progress.id, UUID)
        assert progress.import_run_id == run_id
        assert progress.stage_name == "hashing"
        assert progress.stage_type == StageType.PROCESSOR
        assert progress.entity_type == EntityType.MEDIA
        assert progress.total == 0
        assert progress.completed == 0
        assert progress.failed == 0
        assert progress.status == ImportStatus.PENDING
        assert progress.started_at is None
        assert progress.completed_at is None

    def test_progress_percent_normal(self) -> None:
        """progress_percent calculates correctly for normal values."""
        progress = ProcessingProgress(
            import_run_id=uuid4(),
            stage_name="metadata",
            stage_type=StageType.PROCESSOR,
            entity_type=EntityType.MEDIA,
            total=100,
            completed=40,
            failed=10,
        )
        assert progress.progress_percent == 50.0

    def test_progress_percent_zero_total(self) -> None:
        """progress_percent returns None when total is zero."""
        progress = ProcessingProgress(
            import_run_id=uuid4(),
            stage_name="metadata",
            stage_type=StageType.PROCESSOR,
            entity_type=EntityType.MEDIA,
            total=0,
        )
        assert progress.progress_percent is None

    def test_progress_percent_complete(self) -> None:
        """progress_percent returns 100 when all items processed."""
        progress = ProcessingProgress(
            import_run_id=uuid4(),
            stage_name="hashing",
            stage_type=StageType.PROCESSOR,
            entity_type=EntityType.MEDIA,
            total=50,
            completed=50,
            failed=0,
        )
        assert progress.progress_percent == 100.0

    def test_is_finished_pending(self) -> None:
        """is_finished returns False for pending status."""
        progress = ProcessingProgress(
            import_run_id=uuid4(),
            stage_name="hashing",
            stage_type=StageType.PROCESSOR,
            entity_type=EntityType.MEDIA,
            status=ImportStatus.PENDING,
        )
        assert progress.is_finished is False

    def test_is_finished_running(self) -> None:
        """is_finished returns False for running status."""
        progress = ProcessingProgress(
            import_run_id=uuid4(),
            stage_name="hashing",
            stage_type=StageType.PROCESSOR,
            entity_type=EntityType.MEDIA,
            status=ImportStatus.RUNNING,
        )
        assert progress.is_finished is False

    def test_is_finished_completed(self) -> None:
        """is_finished returns True for completed status."""
        progress = ProcessingProgress(
            import_run_id=uuid4(),
            stage_name="hashing",
            stage_type=StageType.PROCESSOR,
            entity_type=EntityType.MEDIA,
            status=ImportStatus.COMPLETED,
        )
        assert progress.is_finished is True

    def test_is_finished_failed(self) -> None:
        """is_finished returns True for failed status."""
        progress = ProcessingProgress(
            import_run_id=uuid4(),
            stage_name="hashing",
            stage_type=StageType.PROCESSOR,
            entity_type=EntityType.MEDIA,
            status=ImportStatus.FAILED,
        )
        assert progress.is_finished is True

    def test_linker_stage_type(self) -> None:
        """ProcessingProgress works with linker stage type."""
        progress = ProcessingProgress(
            import_run_id=uuid4(),
            stage_name="temporal",
            stage_type=StageType.LINKER,
            entity_type=EntityType.MEDIA,
            total=200,
            completed=100,
        )
        assert progress.stage_type == StageType.LINKER
        assert progress.progress_percent == 50.0
