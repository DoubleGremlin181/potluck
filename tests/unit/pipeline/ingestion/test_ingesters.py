"""Tests for ingestion infrastructure."""

import tempfile
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from potluck.core.celery import (
    is_fatal_error,
    is_transient_error,
)
from potluck.core.exceptions import ConfigurationError, IngestionError
from potluck.models.base import BaseEntity, EntityType, SourceType
from potluck.pipeline import (
    BaseIngestionStage,
    DetectionResult,
    DiscoveryResult,
    PipelineFilter,
    PipelineStats,
    clear_registry,
    detect_stage,
    discover,
    list_stages,
    register,
)
from potluck.pipeline.utils.archive import (
    extract_archive,
    extracted,
    get_archive_type,
    is_archive,
)
from potluck.pipeline.utils.hashing import compute_content_hash, compute_file_hash
from potluck.pipeline.utils.parsers import (
    parse_csv,
    parse_datetime,
    parse_json,
)


class TestEntityType:
    """Tests for EntityType enum in base.py."""

    def test_all_entity_types_defined(self) -> None:
        """All expected entity types are defined."""
        expected = {
            "media",
            "chat_message",
            "email",
            "social_post",
            "social_comment",
            "knowledge_note",
            "calendar_event",
            "transaction",
            "location",
            "location_visit",
            "browsing_history",
            "bookmark",
            "subscription",
            "budget",
            "person",
            "tag",
        }
        actual = {et.value for et in EntityType}
        assert actual == expected

    def test_entity_type_is_string(self) -> None:
        """EntityType values are strings."""
        assert EntityType.MEDIA.value == "media"
        assert isinstance(EntityType.CHAT_MESSAGE.value, str)


class TestPipelineFilter:
    """Tests for PipelineFilter dataclass."""

    def test_default_values(self) -> None:
        """Filter has None defaults."""
        f = PipelineFilter()
        assert f.since is None
        assert f.until is None

    def test_with_dates(self) -> None:
        """Filter can be created with dates."""
        since = datetime(2024, 1, 1, tzinfo=UTC)
        until = datetime(2024, 12, 31, tzinfo=UTC)
        f = PipelineFilter(since=since, until=until)
        assert f.since == since
        assert f.until == until


class TestIngesterRegistry:
    """Tests for ingestion stage registration."""

    def setup_method(self) -> None:
        """Clear registry before each test."""
        clear_registry()

    def test_register_stage(self) -> None:
        """Can register an ingestion stage class."""

        @register
        class MockIngester(BaseIngestionStage):
            SOURCE_TYPE = SourceType.GENERIC
            FILENAME_PATTERNS = [r"mock-.*"]
            SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA}

            def detect(self, path: Path) -> DetectionResult:
                return DetectionResult()

            def execute(
                self,
                path: Path,
                entity_types: set[EntityType] | None = None,
                filters: PipelineFilter | None = None,
            ) -> Iterator[BaseEntity]:
                yield from []

        assert MockIngester in list_stages()

    def test_detect_no_match(self) -> None:
        """detect_stage() returns None when no pattern matches."""
        result = detect_stage(Path("unknown-file.xyz"))
        assert result is None

    def test_detect_with_pattern_match(self) -> None:
        """detect_stage() returns stage when pattern matches."""

        @register
        class MockIngester(BaseIngestionStage):
            SOURCE_TYPE = SourceType.GENERIC
            FILENAME_PATTERNS = [r"test-export-.*\.zip"]
            SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA}

            def detect(self, path: Path) -> DetectionResult:
                return DetectionResult()

            def execute(
                self,
                path: Path,
                entity_types: set[EntityType] | None = None,
                filters: PipelineFilter | None = None,
            ) -> Iterator[BaseEntity]:
                yield from []

        result = detect_stage(Path("test-export-2024.zip"))
        assert result is MockIngester

        result = detect_stage(Path("other-file.zip"))
        assert result is None


class TestArchiveUtils:
    """Tests for archive extraction utilities."""

    def test_is_archive_zip(self) -> None:
        """ZIP files are recognized as archives."""
        with tempfile.NamedTemporaryFile(suffix=".zip") as f:
            assert is_archive(Path(f.name))

    def test_is_archive_tar(self) -> None:
        """TAR files are recognized as archives."""
        with tempfile.NamedTemporaryFile(suffix=".tar") as f:
            assert is_archive(Path(f.name))

    def test_is_archive_tgz(self) -> None:
        """TGZ files are recognized as archives."""
        with tempfile.NamedTemporaryFile(suffix=".tgz") as f:
            assert is_archive(Path(f.name))

    def test_is_archive_directory(self) -> None:
        """Directories are not archives."""
        with tempfile.TemporaryDirectory() as d:
            assert not is_archive(Path(d))

    def test_get_archive_type(self) -> None:
        """Archive type detection works."""
        assert get_archive_type(Path("test.zip")) == "zip"
        assert get_archive_type(Path("test.tar")) == "tar"
        assert get_archive_type(Path("test.tgz")) == "tgz"
        assert get_archive_type(Path("test.tar.gz")) == "tgz"
        assert get_archive_type(Path("test.txt")) is None

    def test_extract_zip(self) -> None:
        """ZIP extraction works."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test ZIP file
            zip_path = Path(tmpdir) / "test.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test.txt", "Hello, World!")

            # Extract it
            result = extract_archive(zip_path)
            try:
                assert result.source_path == zip_path
                assert result.extract_path.exists()
                assert (result.extract_path / "test.txt").exists()
                assert (result.extract_path / "test.txt").read_text() == "Hello, World!"
            finally:
                result.cleanup()

    def test_extracted_context_manager(self) -> None:
        """extracted() context manager works."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test ZIP file
            zip_path = Path(tmpdir) / "test.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("data.txt", "Test content")

            # Use context manager
            with extracted(zip_path) as content_path:
                assert content_path.exists()
                assert (content_path / "data.txt").exists()

            # Temp dir should be cleaned up (may not exist anymore)

    def test_extracted_with_directory(self) -> None:
        """extracted() passes through directories unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            with extracted(tmppath) as content_path:
                assert content_path == tmppath


class TestParserUtils:
    """Tests for parsing utilities."""

    def test_parse_datetime_iso(self) -> None:
        """ISO 8601 dates are parsed correctly."""
        dt = parse_datetime("2024-06-15T12:30:00Z")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 6
        assert dt.day == 15
        assert dt.hour == 12
        assert dt.minute == 30

    def test_parse_datetime_unix_seconds(self) -> None:
        """Unix timestamps in seconds are parsed."""
        dt = parse_datetime(1718453400)  # 2024-06-15 12:30:00 UTC
        assert dt is not None
        assert dt.year == 2024

    def test_parse_datetime_unix_milliseconds(self) -> None:
        """Unix timestamps in milliseconds are parsed."""
        dt = parse_datetime(1718453400000)
        assert dt is not None
        assert dt.year == 2024

    def test_parse_datetime_none(self) -> None:
        """None input returns None."""
        assert parse_datetime(None) is None

    def test_parse_datetime_invalid(self) -> None:
        """Invalid input returns None."""
        assert parse_datetime("not a date") is None

    def test_parse_json(self) -> None:
        """JSON parsing works."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"name": "test", "count": 42}')
            f.flush()
            path = Path(f.name)

        try:
            data = parse_json(path)
            assert isinstance(data, dict)
            assert data["name"] == "test"
            assert data["count"] == 42
        finally:
            path.unlink()

    def test_parse_json_with_date_fields(self) -> None:
        """JSON parsing converts date fields."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"created": "2024-06-15T12:00:00Z", "name": "test"}')
            f.flush()
            path = Path(f.name)

        try:
            data = parse_json(path, date_fields=["created"])
            assert isinstance(data, dict)
            assert isinstance(data["created"], datetime)
            assert data["created"].year == 2024
        finally:
            path.unlink()

    def test_parse_csv(self) -> None:
        """CSV parsing works."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("name,count,active\n")
            f.write("test,42,true\n")
            f.write("other,0,false\n")
            f.flush()
            path = Path(f.name)

        try:
            rows = list(parse_csv(path))
            assert len(rows) == 2
            assert rows[0]["name"] == "test"
            assert rows[0]["count"] == 42
            assert rows[0]["active"] is True
            assert rows[1]["active"] is False
        finally:
            path.unlink()


class TestHashingUtils:
    """Tests for hashing utilities."""

    def test_compute_file_hash(self) -> None:
        """File hashing works."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"Hello, World!")
            f.flush()
            path = Path(f.name)

        try:
            h = compute_file_hash(path)
            assert len(h) == 64  # SHA256 hex length
            assert h == compute_file_hash(path)  # Deterministic
        finally:
            path.unlink()

    def test_compute_content_hash_string(self) -> None:
        """Content hashing works for strings."""
        h1 = compute_content_hash("Hello, World!")
        h2 = compute_content_hash("Hello, World!")
        h3 = compute_content_hash("Different content")

        assert len(h1) == 64
        assert h1 == h2
        assert h1 != h3

    def test_compute_content_hash_bytes(self) -> None:
        """Content hashing works for bytes."""
        h = compute_content_hash(b"Hello, World!")
        assert len(h) == 64


class TestPipelineStats:
    """Tests for PipelineStats."""

    def test_pipeline_stats_default(self) -> None:
        """PipelineStats has zero defaults."""
        stats = PipelineStats()
        assert stats.entities_created == 0
        assert stats.entities_updated == 0
        assert stats.entities_skipped == 0
        assert stats.entities_failed == 0

    def test_pipeline_stats_total(self) -> None:
        """total_processed sums all counts."""
        stats = PipelineStats(
            entities_created=10,
            entities_updated=5,
            entities_skipped=3,
            entities_failed=2,
        )
        assert stats.total_processed == 20


class TestDiscovery:
    """Tests for source discovery."""

    def setup_method(self) -> None:
        """Clear registry before each test."""
        clear_registry()

    def test_discover_nonexistent_path(self) -> None:
        """discover() raises for nonexistent paths."""
        with pytest.raises(IngestionError):
            discover(Path("/nonexistent/path"))

    def test_discover_empty_directory(self) -> None:
        """discover() handles empty directories with no matching stage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = discover(Path(tmpdir))
            # No stage matches, so is_generic=True and no content
            assert result.is_generic
            assert not result.has_content

    def test_discover_with_registered_stage(self) -> None:
        """discover() uses registered stage when pattern matches."""

        @register
        class MockIngester(BaseIngestionStage):
            SOURCE_TYPE = SourceType.GENERIC
            FILENAME_PATTERNS = [r"test-data"]
            SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA}

            def detect(self, path: Path) -> DetectionResult:
                # Count files in the directory
                count = sum(1 for f in path.rglob("*") if f.is_file())
                return DetectionResult(entity_counts={EntityType.MEDIA: count})

            def execute(
                self,
                path: Path,
                entity_types: set[EntityType] | None = None,
                filters: PipelineFilter | None = None,
            ) -> Iterator[BaseEntity]:
                yield from []

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a directory matching the pattern
            test_dir = Path(tmpdir) / "test-data"
            test_dir.mkdir()
            (test_dir / "photo1.jpg").write_bytes(b"fake jpeg")
            (test_dir / "photo2.png").write_bytes(b"fake png")

            result = discover(test_dir)
            assert not result.is_generic  # Stage matched
            assert result.has_content
            assert EntityType.MEDIA in result.available_entities
            assert result.available_entities[EntityType.MEDIA] == 2


class TestDiscoveryResult:
    """Tests for DiscoveryResult dataclass."""

    def test_discovery_result_is_generic(self) -> None:
        """is_generic returns True when no stage."""
        result = DiscoveryResult(source_path=Path("/test"))
        assert result.is_generic

    def test_discovery_result_has_content(self) -> None:
        """has_content returns True when entities found."""
        result = DiscoveryResult(
            source_path=Path("/test"),
            available_entities={EntityType.MEDIA: 5},
        )
        assert result.has_content

    def test_discovery_result_source_type(self) -> None:
        """source_type returns GENERIC when no stage."""
        result = DiscoveryResult(source_path=Path("/test"))
        assert result.source_type == SourceType.GENERIC


class TestPipelineFilterValidation:
    """Tests for PipelineFilter validation."""

    def test_valid_date_range(self) -> None:
        """Valid date range (since < until) passes validation."""
        since = datetime(2024, 1, 1, tzinfo=UTC)
        until = datetime(2024, 12, 31, tzinfo=UTC)
        f = PipelineFilter(since=since, until=until)
        assert f.since == since
        assert f.until == until

    def test_invalid_date_range_raises(self) -> None:
        """Invalid date range (since > until) raises ValueError."""
        since = datetime(2024, 12, 31, tzinfo=UTC)
        until = datetime(2024, 1, 1, tzinfo=UTC)
        with pytest.raises(ValueError, match="'since' must be before 'until'"):
            PipelineFilter(since=since, until=until)

    def test_equal_dates_valid(self) -> None:
        """Equal since and until dates are valid (single moment in time)."""
        date = datetime(2024, 6, 15, tzinfo=UTC)
        f = PipelineFilter(since=date, until=date)
        assert f.since == f.until


class TestBaseIngestionStageValidation:
    """Tests for BaseIngestionStage subclass validation."""

    def setup_method(self) -> None:
        """Clear registry before each test."""
        clear_registry()

    def test_missing_source_type_raises(self) -> None:
        """Subclass without SOURCE_TYPE raises ConfigurationError."""
        with pytest.raises(ConfigurationError, match="must define class attribute 'SOURCE_TYPE'"):

            class BadIngester(BaseIngestionStage):
                FILENAME_PATTERNS = [r"test-.*"]
                SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA}

                def detect(self, path: Path) -> DetectionResult:
                    return DetectionResult()

                def execute(
                    self,
                    path: Path,
                    entity_types: set[EntityType] | None = None,
                    filters: PipelineFilter | None = None,
                ) -> Iterator[BaseEntity]:
                    yield from []

    def test_missing_filename_patterns_raises(self) -> None:
        """Subclass without FILENAME_PATTERNS raises ConfigurationError."""
        with pytest.raises(
            ConfigurationError, match="must define class attribute 'FILENAME_PATTERNS'"
        ):

            class BadIngester(BaseIngestionStage):
                SOURCE_TYPE = SourceType.GENERIC
                SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA}

                def detect(self, path: Path) -> DetectionResult:
                    return DetectionResult()

                def execute(
                    self,
                    path: Path,
                    entity_types: set[EntityType] | None = None,
                    filters: PipelineFilter | None = None,
                ) -> Iterator[BaseEntity]:
                    yield from []

    def test_valid_stage_passes_validation(self) -> None:
        """Properly defined ingestion stage passes validation."""

        class ValidIngester(BaseIngestionStage):
            SOURCE_TYPE = SourceType.GENERIC
            FILENAME_PATTERNS = [r"test-.*"]
            SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA}

            def detect(self, path: Path) -> DetectionResult:
                return DetectionResult()

            def execute(
                self,
                path: Path,
                entity_types: set[EntityType] | None = None,
                filters: PipelineFilter | None = None,
            ) -> Iterator[BaseEntity]:
                yield from []

        # Should not raise
        assert ValidIngester.SOURCE_TYPE == SourceType.GENERIC


class TestRegisterDecorator:
    """Tests for register() decorator validation."""

    def setup_method(self) -> None:
        """Clear registry before each test."""
        clear_registry()

    def test_register_requires_source_type(self) -> None:
        """register() raises ConfigurationError if SOURCE_TYPE is missing."""
        # The register function checks hasattr for SOURCE_TYPE
        with pytest.raises(ConfigurationError, match="must define SOURCE_TYPE"):
            from potluck.pipeline.ingestion import register as register_fn

            # Create a minimal class without SOURCE_TYPE
            class NoSourceType:
                pass

            register_fn(NoSourceType)  # type: ignore[arg-type]


class TestCeleryTaskHelpers:
    """Tests for Celery task helper functions."""

    def testis_transient_error_operational_error(self) -> None:
        """OperationalError is classified as transient."""
        from sqlalchemy.exc import OperationalError

        exc = OperationalError("db connection lost", None, Exception("db error"))
        assert is_transient_error(exc) is True

    def testis_transient_error_interface_error(self) -> None:
        """InterfaceError is classified as transient."""
        from sqlalchemy.exc import InterfaceError

        exc = InterfaceError("interface error", None, Exception("interface error"))
        assert is_transient_error(exc) is True

    def testis_transient_error_disk_io(self) -> None:
        """Disk I/O errors (EIO) are classified as transient."""
        exc = OSError(5, "Input/output error")
        assert is_transient_error(exc) is True

    def testis_transient_error_disk_full(self) -> None:
        """Disk full errors (ENOSPC) are classified as transient."""
        exc = OSError(28, "No space left on device")
        assert is_transient_error(exc) is True

    def testis_transient_error_regular_exception(self) -> None:
        """Regular exceptions are not classified as transient."""
        exc = ValueError("not transient")
        assert is_transient_error(exc) is False

    def testis_fatal_error_file_not_found(self) -> None:
        """FileNotFoundError is classified as fatal."""
        exc = FileNotFoundError("file missing")
        assert is_fatal_error(exc) is True

    def testis_fatal_error_permission_error(self) -> None:
        """PermissionError is classified as fatal."""
        exc = PermissionError("access denied")
        assert is_fatal_error(exc) is True

    def testis_fatal_error_regular_exception(self) -> None:
        """Regular exceptions are not classified as fatal."""
        exc = ValueError("not fatal")
        assert is_fatal_error(exc) is False


class TestCeleryTaskEntityTypeValidation:
    """Tests for entity type validation in Celery tasks."""

    def test_invalid_entity_type_value(self) -> None:
        """Invalid entity type string raises ValueError."""
        with pytest.raises(ValueError):
            EntityType("not_a_valid_type")

    def test_valid_entity_type_value(self) -> None:
        """Valid entity type strings parse correctly."""
        assert EntityType("media") == EntityType.MEDIA
        assert EntityType("email") == EntityType.EMAIL


class TestGetInstructionsGenericSourceTypes:
    """Tests for get_instructions with GENERIC source type stages.

    Multiple stages (MboxStage, ImageFolderStage, TextFilesStage) share
    SOURCE_TYPE=GENERIC. The fix uses cls.__module__ via _stage_package()
    to resolve each to its own package rather than a non-existent
    'ingestion.generic' directory.
    """

    def test_mbox_stage_get_instructions(self) -> None:
        """MboxStage loads instructions from mbox/ package despite GENERIC source type."""
        from potluck.pipeline.ingestion.mbox import MboxStage

        instructions = MboxStage.get_instructions()
        assert isinstance(instructions, str)
        assert len(instructions) > 0

    def test_image_folder_stage_get_instructions(self) -> None:
        """ImageFolderStage loads instructions from image_folder/ package."""
        from potluck.pipeline.ingestion.image_folder import ImageFolderStage

        instructions = ImageFolderStage.get_instructions()
        assert isinstance(instructions, str)
        assert len(instructions) > 0

    def test_text_files_stage_get_instructions(self) -> None:
        """TextFilesStage loads instructions from text_files/ package."""
        from potluck.pipeline.ingestion.text_files import TextFilesStage

        instructions = TextFilesStage.get_instructions()
        assert isinstance(instructions, str)
        assert len(instructions) > 0

    def test_generic_stages_have_different_instructions(self) -> None:
        """Each GENERIC stage loads its own unique instructions, not the same file."""
        from potluck.pipeline.ingestion.image_folder import ImageFolderStage
        from potluck.pipeline.ingestion.mbox import MboxStage
        from potluck.pipeline.ingestion.text_files import TextFilesStage

        mbox_instructions = MboxStage.get_instructions()
        image_instructions = ImageFolderStage.get_instructions()
        text_instructions = TextFilesStage.get_instructions()

        # All three share SOURCE_TYPE=GENERIC but should have different instructions
        assert mbox_instructions != image_instructions
        assert mbox_instructions != text_instructions
        assert image_instructions != text_instructions
