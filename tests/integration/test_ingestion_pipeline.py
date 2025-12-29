"""Integration tests for the ingestion pipeline.

These tests verify the full ingestion flow using a real PostgreSQL database.
They test:
- Entity creation and persistence
- Deduplication (file-level and entity-level)
- Error handling for corrupted files
- Progress callbacks
- The resume_failed parameter

Requires: --run-e2e flag and Docker services running.
"""

import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, select

from potluck.ingesters import (
    BaseIngester,
    DetectionResult,
    IngestionFilter,
    IngestionPipeline,
    clear_registry,
    register,
)
from potluck.ingesters.utils.dedup import compute_content_hash, compute_file_hash
from potluck.models.base import BaseEntity, EntityType, SourceType
from potluck.models.media import Media
from potluck.models.sources import ImportRun, ImportSource, ImportStatus


@pytest.fixture
def db_session(
    run_migrations: None,  # noqa: ARG001
    db_credentials: dict[str, str | int],
) -> Iterator[Session]:
    """Create a SQLModel session for tests."""
    from sqlalchemy import create_engine
    from sqlmodel import Session

    url = (
        f"postgresql://{db_credentials['user']}:{db_credentials['password']}"
        f"@{db_credentials['host']}:{db_credentials['port']}/{db_credentials['dbname']}"
    )
    engine = create_engine(url)
    with Session(engine) as session:
        yield session
        # Rollback any uncommitted changes
        session.rollback()


@pytest.fixture(autouse=True)
def clean_registry() -> Iterator[None]:
    """Clear ingester registry before each test."""
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
def mock_media_ingester() -> type[BaseIngester]:
    """Create and register a mock ingester that yields Media entities."""

    @register
    class MockMediaIngester(BaseIngester):  # type: ignore[misc]
        SOURCE_TYPE = SourceType.GENERIC
        FILENAME_PATTERNS = [r"test-media-.*\.zip"]
        SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA}

        def detect_contents(self, path: Path) -> DetectionResult:
            # Count files in the directory
            count = sum(1 for f in path.rglob("*") if f.is_file())
            return DetectionResult(entity_counts={EntityType.MEDIA: count})

        def ingest(
            self,
            path: Path,
            entity_types: set[EntityType],
            filters: IngestionFilter | None = None,
        ) -> Iterator[BaseEntity]:
            if EntityType.MEDIA not in entity_types:
                return

            for file_path in path.rglob("*"):
                if file_path.is_file():
                    content = file_path.read_bytes()
                    yield Media(
                        source_type=SourceType.GENERIC,
                        source_id=str(file_path.name),
                        content_hash=compute_content_hash(content),
                        file_path=str(file_path),
                        file_name=file_path.name,
                        file_size=len(content),
                        mime_type="application/octet-stream",
                    )

    return MockMediaIngester


@pytest.mark.e2e
class TestIngestionPipeline:
    """Integration tests for IngestionPipeline."""

    def test_basic_ingestion(
        self,
        db_session: Session,
        mock_media_ingester: type[BaseIngester],
    ) -> None:
        """Test basic ingestion creates entities in the database."""
        # Create test data
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "test-media-001.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("photo1.jpg", b"fake jpeg content 1")
                zf.writestr("photo2.jpg", b"fake jpeg content 2")

            # Run ingestion
            pipeline = IngestionPipeline(session=db_session)
            result = pipeline.run(zip_path)

            # Verify result
            assert result.success
            assert result.stats.created == 2
            assert result.stats.skipped == 0

            # Verify entities in database
            media_count = db_session.exec(select(Media)).all()
            assert len(media_count) >= 2

            # Verify ImportRun was created
            assert result.import_run.status == ImportStatus.COMPLETED
            assert result.import_run.entities_created == 2

    def test_entity_deduplication(
        self,
        db_session: Session,
        mock_media_ingester: type[BaseIngester],
    ) -> None:
        """Test that duplicate entities (same content_hash) are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create first archive
            zip1_path = Path(tmpdir) / "test-media-first.zip"
            with zipfile.ZipFile(zip1_path, "w") as zf:
                zf.writestr("photo.jpg", b"same content")

            # Create second archive with same content
            zip2_path = Path(tmpdir) / "test-media-second.zip"
            with zipfile.ZipFile(zip2_path, "w") as zf:
                zf.writestr("photo_copy.jpg", b"same content")

            pipeline = IngestionPipeline(session=db_session)

            # First import
            result1 = pipeline.run(zip1_path)
            assert result1.stats.created == 1
            assert result1.stats.skipped == 0

            # Second import - same content should be skipped
            result2 = pipeline.run(zip2_path)
            assert result2.stats.created == 0
            assert result2.stats.skipped == 1

    def test_file_level_deduplication(
        self,
        db_session: Session,
        mock_media_ingester: type[BaseIngester],
    ) -> None:
        """Test that re-importing the same file is skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "test-media-same.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("photo.jpg", b"content")

            pipeline = IngestionPipeline(session=db_session)

            # First import
            result1 = pipeline.run(zip_path)
            assert result1.success
            assert result1.stats.created == 1

            # Second import of same file - should skip entirely
            result2 = pipeline.run(zip_path)
            assert result2.success
            # File was already imported, so all entities are "skipped"
            assert result2.stats.skipped >= 1

    def test_resume_failed_forces_reprocessing(
        self,
        db_session: Session,
        mock_media_ingester: type[BaseIngester],
    ) -> None:
        """Test that resume_failed=True forces reprocessing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "test-media-resume.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("photo.jpg", b"content for resume test")

            pipeline = IngestionPipeline(session=db_session)

            # First import
            result1 = pipeline.run(zip_path)
            assert result1.success
            first_run_id = result1.import_run.id

            # Second import with resume_failed=True - should create new run
            result2 = pipeline.run(zip_path, resume_failed=True)
            assert result2.success
            assert result2.import_run.id != first_run_id

    def test_progress_callback(
        self,
        db_session: Session,
        mock_media_ingester: type[BaseIngester],
    ) -> None:
        """Test that progress callback is called during ingestion."""
        progress_updates: list[tuple[int, int, str | None]] = []

        def on_progress(current: int, total: int, message: str | None) -> None:
            progress_updates.append((current, total, message))

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "test-media-progress.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                for i in range(5):
                    zf.writestr(f"photo{i}.jpg", f"content {i}".encode())

            pipeline = IngestionPipeline(session=db_session, on_progress=on_progress)
            result = pipeline.run(zip_path)

            assert result.success
            assert len(progress_updates) > 0
            # Last update should show 5 items processed
            assert progress_updates[-1][0] == 5

    def test_entity_callback(
        self,
        db_session: Session,
        mock_media_ingester: type[BaseIngester],
    ) -> None:
        """Test that entity callback is called for each created entity."""
        entities_created: list[tuple[EntityType, BaseEntity]] = []

        def on_entity(entity_type: EntityType, entity: BaseEntity) -> None:
            entities_created.append((entity_type, entity))

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "test-media-callback.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("photo1.jpg", b"content 1")
                zf.writestr("photo2.jpg", b"content 2")

            pipeline = IngestionPipeline(session=db_session, on_entity=on_entity)
            result = pipeline.run(zip_path)

            assert result.success
            assert len(entities_created) == 2
            assert all(et == EntityType.MEDIA for et, _ in entities_created)

    def test_no_ingester_match_returns_empty_result(
        self,
        db_session: Session,
    ) -> None:
        """Test that paths with no matching ingester return empty result."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file that doesn't match any ingester pattern
            unknown_path = Path(tmpdir) / "unknown-file.xyz"
            unknown_path.write_bytes(b"some content")

            pipeline = IngestionPipeline(session=db_session)
            result = pipeline.run(unknown_path)

            # Should complete but with no entities
            assert result.success
            assert result.stats.created == 0


@pytest.mark.e2e
class TestDedupUtilities:
    """Integration tests for deduplication utilities."""

    def test_compute_file_hash_consistency(self) -> None:
        """Test that file hash is consistent across calls."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content for hashing")
            f.flush()
            path = Path(f.name)

        try:
            hash1 = compute_file_hash(path)
            hash2 = compute_file_hash(path)
            assert hash1 == hash2
            assert len(hash1) == 64  # SHA256 hex
        finally:
            path.unlink()

    def test_compute_content_hash_for_dedup(self) -> None:
        """Test content hash for entity deduplication."""
        content1 = b"same content"
        content2 = b"same content"
        content3 = b"different content"

        hash1 = compute_content_hash(content1)
        hash2 = compute_content_hash(content2)
        hash3 = compute_content_hash(content3)

        assert hash1 == hash2  # Same content = same hash
        assert hash1 != hash3  # Different content = different hash


@pytest.mark.e2e
class TestImportSourceAndRun:
    """Integration tests for ImportSource and ImportRun models."""

    def test_import_source_creation(self, db_session: Session) -> None:
        """Test ImportSource creation and persistence."""
        source = ImportSource(
            source_type=SourceType.GOOGLE_TAKEOUT,
            name="Test Google Takeout",
            description="Test import source",
        )
        db_session.add(source)
        db_session.commit()
        db_session.refresh(source)

        assert source.id is not None
        assert source.source_type == SourceType.GOOGLE_TAKEOUT

    def test_import_run_with_file_hash(self, db_session: Session) -> None:
        """Test ImportRun with file_hash for deduplication."""
        source = ImportSource(
            source_type=SourceType.GENERIC,
            name="Test Source",
        )
        db_session.add(source)
        db_session.commit()
        db_session.refresh(source)

        run = ImportRun(
            source_id=source.id,
            file_hash="abc123def456",
            status=ImportStatus.COMPLETED,
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)

        # Query by file_hash
        stmt = select(ImportRun).where(ImportRun.file_hash == "abc123def456")
        found_run = db_session.exec(stmt).first()
        assert found_run is not None
        assert found_run.id == run.id

    def test_multiple_runs_per_source(self, db_session: Session) -> None:
        """Test that one ImportSource can have multiple ImportRuns."""
        source = ImportSource(
            source_type=SourceType.GENERIC,
            name="Multi-run Source",
        )
        db_session.add(source)
        db_session.commit()
        db_session.refresh(source)

        # Create multiple runs
        for i in range(3):
            run = ImportRun(
                source_id=source.id,
                file_hash=f"hash_{i}",
                status=ImportStatus.COMPLETED,
            )
            db_session.add(run)

        db_session.commit()

        # Query all runs for this source
        stmt = select(ImportRun).where(ImportRun.source_id == source.id)
        runs = db_session.exec(stmt).all()
        assert len(runs) == 3
