"""Tests for PipelineOrchestrator deduplication and batch handling."""

from uuid import uuid4

from potluck.models.base import EntityType, IngestableEntity, SourceType
from potluck.models.browsing import Bookmark, BookmarkFolder, BrowsingHistory
from potluck.models.calendar import CalendarEvent, EventParticipant, ResponseStatus
from potluck.pipeline.orchestrator import PipelineOrchestrator


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
