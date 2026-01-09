"""Tests for ImportSource and ImportRun models."""

from uuid import UUID, uuid4

from potluck.models.base import SourceType
from potluck.models.sources import ImportRun, ImportSource, ImportStatus


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
