"""Tests for PipelineOrchestrator deduplication and batch handling."""

from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from potluck.models.base import EntityType, IngestableEntity, SourceType
from potluck.models.browsing import Bookmark, BookmarkFolder, BrowsingHistory
from potluck.models.calendar import CalendarEvent, EventParticipant, ResponseStatus
from potluck.models.sources import ImportRun, ImportSource, ImportStatus
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.orchestrator import PipelineOrchestrator, discover


class MockSession:
    """Mock session for testing without database."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.committed = 0

    def add(self, entity: object) -> None:
        self.added.append(entity)

    def commit(self) -> None:
        self.committed += 1

    def exec(self, stmt: object) -> "MockResult":  # noqa: ARG002
        return MockResult()


class MockResult:
    """Mock query result."""

    def first(self) -> None:
        return None


class TestDeduplication:
    """Tests for content_hash deduplication."""

    def test_duplicate_content_hash_detected(self) -> None:
        """Entities with same content_hash are marked as duplicates."""
        session = MockSession()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        # Create two entities with same content hash
        entity1 = BrowsingHistory(
            source_type=SourceType.GOOGLE_TAKEOUT,
            url="https://example.com",
            url_hash="abc123",
            content_hash="same_hash",
        )
        entity2 = BrowsingHistory(
            source_type=SourceType.GOOGLE_TAKEOUT,
            url="https://example.com/page",
            url_hash="def456",
            content_hash="same_hash",  # Same hash
        )

        # First entity is not a duplicate
        assert not orchestrator._is_duplicate(entity1)

        # Second entity with same hash is a duplicate
        assert orchestrator._is_duplicate(entity2)

    def test_different_content_hash_not_duplicate(self) -> None:
        """Entities with different content_hash are not duplicates."""
        session = MockSession()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        entity1 = BrowsingHistory(
            source_type=SourceType.GOOGLE_TAKEOUT,
            url="https://example.com",
            url_hash="abc123",
            content_hash="hash1",
        )
        entity2 = BrowsingHistory(
            source_type=SourceType.GOOGLE_TAKEOUT,
            url="https://example.com/page",
            url_hash="def456",
            content_hash="hash2",  # Different hash
        )

        assert not orchestrator._is_duplicate(entity1)
        assert not orchestrator._is_duplicate(entity2)

    def test_entity_without_content_hash_not_duplicate(self) -> None:
        """Entities without content_hash field are never duplicates."""
        session = MockSession()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        # BookmarkFolder has no content_hash field
        folder1 = BookmarkFolder(
            source_type=SourceType.GOOGLE_TAKEOUT,
            name="Test Folder",
        )
        folder2 = BookmarkFolder(
            source_type=SourceType.GOOGLE_TAKEOUT,
            name="Test Folder",  # Same name but no hash
        )

        assert not orchestrator._is_duplicate(folder1)
        assert not orchestrator._is_duplicate(folder2)


class TestFKOrphanPrevention:
    """Tests for foreign key orphan prevention."""

    def test_references_skipped_parent(self) -> None:
        """Child entity referencing skipped parent is detected."""
        session = MockSession()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        # Simulate a skipped parent
        parent_id = uuid4()
        orchestrator._skipped_entity_ids.add(str(parent_id))

        # Create child referencing skipped parent
        child = EventParticipant(
            event_id=parent_id,
            email="test@example.com",
            response_status=ResponseStatus.ACCEPTED,
        )

        assert orchestrator._references_skipped_entity(child)

    def test_does_not_reference_skipped_parent(self) -> None:
        """Child entity not referencing skipped parent passes."""
        session = MockSession()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        # No skipped parents
        child = EventParticipant(
            event_id=uuid4(),
            email="test@example.com",
            response_status=ResponseStatus.ACCEPTED,
        )

        assert not orchestrator._references_skipped_entity(child)

    def test_entity_without_fk_not_orphan(self) -> None:
        """Entity without FK fields is never an orphan."""
        session = MockSession()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        # Add some skipped IDs
        orchestrator._skipped_entity_ids.add(str(uuid4()))

        # BrowsingHistory has no FK to other ingested entities
        entity = BrowsingHistory(
            source_type=SourceType.GOOGLE_TAKEOUT,
            url="https://example.com",
            url_hash="abc123",
        )

        assert not orchestrator._references_skipped_entity(entity)

    def test_multiple_fk_fields_checked(self) -> None:
        """All FK fields are checked for orphan detection."""
        session = MockSession()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        # Skipped folder
        folder_id = uuid4()
        orchestrator._skipped_entity_ids.add(str(folder_id))

        # Bookmark referencing skipped folder
        bookmark = Bookmark(
            source_type=SourceType.GOOGLE_TAKEOUT,
            url="https://example.com",
            url_hash="abc123",
            folder_id=folder_id,
        )

        assert orchestrator._references_skipped_entity(bookmark)


class TestBatchSorting:
    """Tests for batch dependency sorting."""

    def test_parent_before_child(self) -> None:
        """Parent entities are sorted before children."""
        session = MockSession()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        # Create a mix of parent and child entities
        event = CalendarEvent(
            source_type=SourceType.GOOGLE_TAKEOUT,
            source_id="event1",
        )
        participant = EventParticipant(
            event_id=event.id,
            email="test@example.com",
            response_status=ResponseStatus.ACCEPTED,
        )

        # Mix them up (annotate as IngestableEntity for type checker)
        batch: list[IngestableEntity] = [participant, event]

        # Sort should put event first
        sorted_batch = orchestrator._sort_by_dependencies(batch)

        assert sorted_batch[0] == event
        assert sorted_batch[1] == participant

    def test_folder_before_bookmark(self) -> None:
        """Bookmark folders are sorted before bookmarks."""
        session = MockSession()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        folder = BookmarkFolder(
            source_type=SourceType.GOOGLE_TAKEOUT,
            name="Test",
        )
        bookmark = Bookmark(
            source_type=SourceType.GOOGLE_TAKEOUT,
            url="https://example.com",
            url_hash="abc123",
            folder_id=folder.id,
        )

        batch: list[IngestableEntity] = [bookmark, folder]
        sorted_batch = orchestrator._sort_by_dependencies(batch)

        assert sorted_batch[0] == folder
        assert sorted_batch[1] == bookmark

    def test_same_priority_stable_sort(self) -> None:
        """Entities with same priority maintain relative order."""
        session = MockSession()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        # Two events - same priority
        event1 = CalendarEvent(
            source_type=SourceType.GOOGLE_TAKEOUT,
            source_id="event1",
        )
        event2 = CalendarEvent(
            source_type=SourceType.GOOGLE_TAKEOUT,
            source_id="event2",
        )

        batch: list[IngestableEntity] = [event1, event2]
        sorted_batch = orchestrator._sort_by_dependencies(batch)

        # Should maintain original order
        assert sorted_batch[0] == event1
        assert sorted_batch[1] == event2


class TestCacheClearOnRun:
    """Tests for cache management between runs."""

    def test_caches_cleared_on_new_run(self) -> None:
        """In-memory caches are cleared for each new run."""
        session = MockSession()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        # Simulate data from a previous run
        orchestrator._seen_hashes.add("old_hash")
        orchestrator._skipped_entity_ids.add("old_id")
        orchestrator._entity_ids_by_type[EntityType.MEDIA] = ["old_entity"]

        # Simulate start of new run (normally done in run())
        orchestrator._seen_hashes.clear()
        orchestrator._entity_ids_by_type.clear()
        orchestrator._skipped_entity_ids.clear()

        assert len(orchestrator._seen_hashes) == 0
        assert len(orchestrator._skipped_entity_ids) == 0
        assert len(orchestrator._entity_ids_by_type) == 0


class TestContentPathParameter:
    """Tests for content_path parameter that avoids double extraction."""

    def test_discover_with_content_path_skips_extraction(self, tmp_path: Path) -> None:
        """discover() with content_path uses it directly instead of extracting."""
        # Create a fake source file (so path.exists() passes)
        source_file = tmp_path / "test.tgz"
        source_file.touch()

        # Create a content directory
        content_dir = tmp_path / "content"
        content_dir.mkdir()

        with (
            patch("potluck.pipeline.orchestrator.extracted") as mock_extracted,
            patch("potluck.pipeline.orchestrator.detect_stage") as mock_detect,
        ):
            mock_detect.return_value = None

            result = discover(source_file, content_path=content_dir)

            # extracted() should NOT be called when content_path is provided
            mock_extracted.assert_not_called()
            assert result.source_path == source_file
            assert result.available_entities == {}

    def test_discover_without_content_path_extracts(self, tmp_path: Path) -> None:
        """discover() without content_path uses extracted() context manager."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()

        with patch("potluck.pipeline.orchestrator.detect_stage") as mock_detect:
            mock_detect.return_value = None

            result = discover(source_dir)

            # Should work fine (directory path, no extraction needed)
            assert result.source_path == source_dir
            assert result.available_entities == {}

    def test_run_with_content_path_skips_extraction(self) -> None:
        """PipelineOrchestrator.run() with content_path skips internal extraction."""
        session = MockSession()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        mock_content = Path("/tmp/fake_content")

        with (
            patch("potluck.pipeline.orchestrator.extracted") as mock_extracted,
            patch("potluck.pipeline.orchestrator.detect_stage") as mock_detect,
            patch.object(orchestrator, "_create_empty_result") as mock_empty,
        ):
            mock_detect.return_value = None
            mock_empty.return_value = MagicMock()

            orchestrator.run(
                Path("/tmp/fake.tgz"),
                content_path=mock_content,
            )

            # extracted() should NOT be called
            mock_extracted.assert_not_called()
            # detect_stage should still be called with original path
            mock_detect.assert_called_once_with(Path("/tmp/fake.tgz"))


class TestImportRunReuse:
    """Tests for reusing existing ImportRun/ImportSource (Celery task path)."""

    def _make_mock_session(self) -> MockSession:
        """Create a mock session that also tracks refreshes."""
        session = MockSession()
        session.refresh = MagicMock()  # type: ignore[attr-defined]
        return session

    def test_existing_import_run_not_duplicated(self) -> None:
        """When import_run is provided, orchestrator does NOT create a new one."""
        session = self._make_mock_session()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        # Pre-create source and run (as start_ingestion would)
        existing_source = ImportSource(
            source_type=SourceType.GENERIC,
            name="test.zip",
        )
        existing_run = ImportRun(
            source_id=existing_source.id,
            status=ImportStatus.PENDING,
            file_hash="abc123",
        )

        with (
            patch("potluck.pipeline.orchestrator.detect_stage") as mock_detect,
            patch("potluck.pipeline.orchestrator.compute_file_hash", return_value="abc123"),
            patch.object(orchestrator, "_find_completed_run", return_value=None),
        ):
            # Set up a mock stage that returns entities
            mock_stage_cls = MagicMock()
            mock_stage = MagicMock()
            mock_stage_cls.return_value = mock_stage
            mock_stage_cls.SOURCE_TYPE = SourceType.YNAB
            mock_stage.detect.return_value = MagicMock(
                entity_counts={EntityType.TRANSACTION: 10},
                metadata={},
            )
            mock_stage.execute.return_value = iter([])  # No entities for simplicity
            mock_detect.return_value = mock_stage_cls

            result = orchestrator.run(
                Path("/tmp/test.zip"),
                content_path=Path("/tmp/content"),
                import_source=existing_source,
                import_run=existing_run,
            )

            # The result should reference the SAME import run, not a new one
            assert result.import_run.id == existing_run.id
            # _create_import_run should not have been called (no new ImportRun in added)
            import_runs_added = [obj for obj in session.added if isinstance(obj, ImportRun)]
            assert len(import_runs_added) == 0

    def test_existing_import_run_status_updated(self) -> None:
        """When import_run is provided, its status transitions correctly."""
        session = self._make_mock_session()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        existing_source = ImportSource(
            source_type=SourceType.GENERIC,
            name="test.zip",
        )
        existing_run = ImportRun(
            source_id=existing_source.id,
            status=ImportStatus.PENDING,
            file_hash="abc123",
        )

        with (
            patch("potluck.pipeline.orchestrator.detect_stage") as mock_detect,
            patch("potluck.pipeline.orchestrator.compute_file_hash", return_value="abc123"),
            patch.object(orchestrator, "_find_completed_run", return_value=None),
            patch.object(orchestrator, "_queue_linkers"),
        ):
            mock_stage_cls = MagicMock()
            mock_stage = MagicMock()
            mock_stage_cls.return_value = mock_stage
            mock_stage_cls.SOURCE_TYPE = SourceType.YNAB
            mock_stage.detect.return_value = MagicMock(
                entity_counts={EntityType.TRANSACTION: 1},
                metadata={},
            )
            mock_stage.execute.return_value = iter([])
            mock_detect.return_value = mock_stage_cls

            result = orchestrator.run(
                Path("/tmp/test.zip"),
                content_path=Path("/tmp/content"),
                import_source=existing_source,
                import_run=existing_run,
            )

            assert result.import_run.status == ImportStatus.COMPLETED
            assert result.import_run.completed_at is not None

    def test_source_type_updated_from_generic(self) -> None:
        """When existing ImportSource is GENERIC, it gets updated with detected type."""
        session = self._make_mock_session()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        existing_source = ImportSource(
            source_type=SourceType.GENERIC,
            name="test.zip",
        )
        existing_run = ImportRun(
            source_id=existing_source.id,
            status=ImportStatus.PENDING,
        )

        with (
            patch("potluck.pipeline.orchestrator.detect_stage") as mock_detect,
            patch("potluck.pipeline.orchestrator.compute_file_hash", return_value=None),
            patch.object(orchestrator, "_find_completed_run", return_value=None),
            patch.object(orchestrator, "_queue_linkers"),
        ):
            mock_stage_cls = MagicMock()
            mock_stage = MagicMock()
            mock_stage_cls.return_value = mock_stage
            mock_stage_cls.SOURCE_TYPE = SourceType.YNAB
            mock_stage.detect.return_value = MagicMock(
                entity_counts={EntityType.TRANSACTION: 1},
                metadata={},
            )
            mock_stage.execute.return_value = iter([])
            mock_detect.return_value = mock_stage_cls

            orchestrator.run(
                Path("/tmp/test.zip"),
                content_path=Path("/tmp/content"),
                import_source=existing_source,
                import_run=existing_run,
            )

            # Source type should be updated from GENERIC to the detected type
            assert existing_source.source_type == SourceType.YNAB

    def test_without_import_run_creates_new(self) -> None:
        """When import_run is NOT provided, orchestrator creates one (default behavior)."""
        session = self._make_mock_session()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        with (
            patch("potluck.pipeline.orchestrator.detect_stage") as mock_detect,
            patch("potluck.pipeline.orchestrator.compute_file_hash", return_value=None),
            patch.object(orchestrator, "_find_completed_run", return_value=None),
            patch.object(orchestrator, "_queue_linkers"),
        ):
            mock_stage_cls = MagicMock()
            mock_stage = MagicMock()
            mock_stage_cls.return_value = mock_stage
            mock_stage_cls.SOURCE_TYPE = SourceType.YNAB
            mock_stage.detect.return_value = MagicMock(
                entity_counts={EntityType.TRANSACTION: 1},
                metadata={},
            )
            mock_stage.execute.return_value = iter([])
            mock_detect.return_value = mock_stage_cls

            result = orchestrator.run(
                Path("/tmp/test.zip"),
                content_path=Path("/tmp/content"),
            )

            # Should have created both ImportSource and ImportRun
            import_sources_added = [obj for obj in session.added if isinstance(obj, ImportSource)]
            import_runs_added = [obj for obj in session.added if isinstance(obj, ImportRun)]
            assert len(import_sources_added) == 1
            assert len(import_runs_added) == 1
            assert result.import_run.status == ImportStatus.COMPLETED


class TestSourceTypeOverride:
    """Tests for source_type_override parameter."""

    def _make_mock_session(self) -> MockSession:
        session = MockSession()
        session.refresh = MagicMock()  # type: ignore[attr-defined]
        return session

    def test_override_uses_get_stage_instead_of_detect(self) -> None:
        """When source_type_override is provided, get_stage is used instead of detect_stage."""
        session = self._make_mock_session()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        mock_stage_cls = MagicMock()
        mock_stage = MagicMock()
        mock_stage_cls.return_value = mock_stage
        mock_stage_cls.SOURCE_TYPE = SourceType.YNAB
        mock_stage.detect.return_value = MagicMock(
            entity_counts={EntityType.TRANSACTION: 5},
            metadata={},
        )
        mock_stage.execute.return_value = iter([])

        existing_source = ImportSource(source_type=SourceType.YNAB, name="test.zip")
        existing_run = ImportRun(source_id=existing_source.id, status=ImportStatus.PENDING)

        with (
            patch("potluck.pipeline.orchestrator.detect_stage") as mock_detect,
            patch(
                "potluck.pipeline.orchestrator.get_stage", return_value=mock_stage_cls
            ) as mock_get,
            patch("potluck.pipeline.orchestrator.compute_file_hash", return_value=None),
            patch.object(orchestrator, "_find_completed_run", return_value=None),
            patch.object(orchestrator, "_queue_linkers"),
        ):
            orchestrator.run(
                Path("/tmp/renamed-file.zip"),
                content_path=Path("/tmp/content"),
                import_source=existing_source,
                import_run=existing_run,
                source_type_override=SourceType.YNAB,
            )

            # get_stage should be called with the override type
            mock_get.assert_called_once_with(SourceType.YNAB)
            # detect_stage should NOT be called
            mock_detect.assert_not_called()

    def test_no_override_uses_detect_stage(self) -> None:
        """Without source_type_override, detect_stage is used (default behavior)."""
        session = self._make_mock_session()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        with (
            patch("potluck.pipeline.orchestrator.detect_stage", return_value=None) as mock_detect,
            patch("potluck.pipeline.orchestrator.get_stage") as mock_get,
            patch("potluck.pipeline.orchestrator.compute_file_hash", return_value=None),
            patch.object(orchestrator, "_create_empty_result") as mock_empty,
        ):
            mock_empty.return_value = MagicMock()

            orchestrator.run(
                Path("/tmp/test.zip"),
                content_path=Path("/tmp/content"),
            )

            # detect_stage should be called
            mock_detect.assert_called_once_with(Path("/tmp/test.zip"))
            # get_stage should NOT be called
            mock_get.assert_not_called()


class TestFilterPassthrough:
    """Tests for PipelineFilter passthrough to stage.execute()."""

    def _make_mock_session(self) -> MockSession:
        session = MockSession()
        session.refresh = MagicMock()  # type: ignore[attr-defined]
        return session

    def test_filters_passed_to_stage_execute(self) -> None:
        """PipelineFilter is passed through to stage.execute() during ingestion."""
        session = self._make_mock_session()
        orchestrator = PipelineOrchestrator(session)  # type: ignore[arg-type]

        mock_stage_cls = MagicMock()
        mock_stage = MagicMock()
        mock_stage_cls.return_value = mock_stage
        mock_stage_cls.SOURCE_TYPE = SourceType.YNAB
        mock_stage.detect.return_value = MagicMock(
            entity_counts={EntityType.TRANSACTION: 3},
            metadata={},
        )
        mock_stage.execute.return_value = iter([])

        existing_source = ImportSource(source_type=SourceType.GENERIC, name="test.zip")
        existing_run = ImportRun(source_id=existing_source.id, status=ImportStatus.PENDING)

        from datetime import UTC, datetime

        filters = PipelineFilter(
            since=datetime(2024, 1, 1, tzinfo=UTC),
            until=datetime(2024, 12, 31, tzinfo=UTC),
        )

        with (
            patch("potluck.pipeline.orchestrator.detect_stage", return_value=mock_stage_cls),
            patch("potluck.pipeline.orchestrator.compute_file_hash", return_value=None),
            patch.object(orchestrator, "_find_completed_run", return_value=None),
            patch.object(orchestrator, "_queue_linkers"),
        ):
            orchestrator.run(
                Path("/tmp/test.zip"),
                content_path=Path("/tmp/content"),
                filters=filters,
                import_source=existing_source,
                import_run=existing_run,
            )

            # stage.execute should receive the filters
            mock_stage.execute.assert_called_once()
            call_args = mock_stage.execute.call_args
            assert call_args[0][0] == Path("/tmp/content")  # content_path
            assert call_args[0][2] is filters  # filters (3rd positional arg)
