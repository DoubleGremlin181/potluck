"""Tests for ingestion infrastructure (Phase 3)."""

import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from potluck.ingesters.base import (
    BaseIngester,
    DetectionResult,
    IngestionFilter,
)
from potluck.ingesters.discover import discover
from potluck.ingesters.hooks import (
    HookRegistry,
    LoggingHook,
    get_hook_registry,
)
from potluck.ingesters.registry import (
    EXTENSION_TO_ENTITY_TYPE,
    IngesterRegistry,
    get_registry,
    register_ingester,
)
from potluck.ingesters.utils.archive import (
    extract_archive,
    extracted,
    get_archive_type,
    is_archive,
)
from potluck.ingesters.utils.dedup import compute_content_hash, compute_file_hash
from potluck.ingesters.utils.parsers import (
    parse_csv,
    parse_datetime,
    parse_json,
)
from potluck.ingesters.utils.progress import (
    IngestionStats,
    LoggingProgressCallback,
)
from potluck.models.base import EntityType, SourceType


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
            "location_visit",
            "browsing_history",
            "bookmark",
            "person",
        }
        actual = {et.value for et in EntityType}
        assert actual == expected

    def test_entity_type_is_string(self) -> None:
        """EntityType values are strings."""
        assert EntityType.MEDIA.value == "media"
        assert isinstance(EntityType.CHAT_MESSAGE.value, str)


class TestIngestionFilter:
    """Tests for IngestionFilter dataclass."""

    def test_default_values(self) -> None:
        """Filter has None defaults."""
        f = IngestionFilter()
        assert f.since is None
        assert f.until is None

    def test_with_dates(self) -> None:
        """Filter can be created with dates."""
        since = datetime(2024, 1, 1, tzinfo=UTC)
        until = datetime(2024, 12, 31, tzinfo=UTC)
        f = IngestionFilter(since=since, until=until)
        assert f.since == since
        assert f.until == until


class TestIngesterRegistry:
    """Tests for IngesterRegistry."""

    def setup_method(self) -> None:
        """Clear registry before each test."""
        get_registry().clear()

    def test_singleton_pattern(self) -> None:
        """Registry is a singleton."""
        r1 = IngesterRegistry()
        r2 = IngesterRegistry()
        assert r1 is r2

    def test_get_registry_returns_singleton(self) -> None:
        """get_registry returns the singleton."""
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_register_ingester(self) -> None:
        """Can register an ingester class."""

        class MockIngester(BaseIngester):
            SOURCE_TYPE = SourceType.GENERIC
            DETECTION_PATTERNS = [r"mock-.*"]
            SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA}
            INSTRUCTIONS = "Test instructions"

            def detect_contents(self, path: Path) -> DetectionResult:
                return DetectionResult()

        registry = get_registry()
        registry.register(MockIngester)
        assert MockIngester in registry.get_all()

    def test_detect_no_match(self) -> None:
        """detect() returns None when no pattern matches."""
        registry = get_registry()
        result = registry.detect(Path("unknown-file.xyz"))
        assert result is None

    def test_detect_with_pattern_match(self) -> None:
        """detect() returns ingester when pattern matches."""

        class MockIngester(BaseIngester):
            SOURCE_TYPE = SourceType.GENERIC
            DETECTION_PATTERNS = [r"test-export-.*\.zip"]
            SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA}
            INSTRUCTIONS = "Test"

            def detect_contents(self, path: Path) -> DetectionResult:
                return DetectionResult()

        registry = get_registry()
        registry.register(MockIngester)

        result = registry.detect(Path("test-export-2024.zip"))
        assert result is MockIngester

        result = registry.detect(Path("other-file.zip"))
        assert result is None

    def test_register_decorator(self) -> None:
        """@register_ingester decorator works."""

        @register_ingester
        class DecoratedIngester(BaseIngester):
            SOURCE_TYPE = SourceType.GENERIC
            DETECTION_PATTERNS = [r"decorated-.*"]
            SUPPORTED_ENTITY_TYPES = {EntityType.MEDIA}
            INSTRUCTIONS = "Test"

            def detect_contents(self, path: Path) -> DetectionResult:
                return DetectionResult()

        assert DecoratedIngester in get_registry().get_all()


class TestExtensionToEntityType:
    """Tests for EXTENSION_TO_ENTITY_TYPE mapping."""

    def test_image_extensions(self) -> None:
        """Image extensions map to MEDIA."""
        for ext in [".jpg", ".jpeg", ".png", ".gif", ".heic"]:
            assert EXTENSION_TO_ENTITY_TYPE[ext] == EntityType.MEDIA

    def test_text_extensions(self) -> None:
        """Text extensions map to KNOWLEDGE_NOTE."""
        for ext in [".txt", ".md", ".markdown"]:
            assert EXTENSION_TO_ENTITY_TYPE[ext] == EntityType.KNOWLEDGE_NOTE

    def test_email_extensions(self) -> None:
        """Email extensions map to EMAIL."""
        assert EXTENSION_TO_ENTITY_TYPE[".mbox"] == EntityType.EMAIL
        assert EXTENSION_TO_ENTITY_TYPE[".eml"] == EntityType.EMAIL


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


class TestDedupUtils:
    """Tests for deduplication utilities."""

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


class TestProgressTracking:
    """Tests for progress tracking utilities."""

    def test_ingestion_stats_default(self) -> None:
        """IngestionStats has zero defaults."""
        stats = IngestionStats()
        assert stats.created == 0
        assert stats.updated == 0
        assert stats.skipped == 0
        assert stats.failed == 0
        assert stats.total_processed == 0

    def test_ingestion_stats_total(self) -> None:
        """total_processed sums all counts."""
        stats = IngestionStats(created=10, updated=5, skipped=3, failed=2)
        assert stats.total_processed == 20

    def test_logging_progress_callback(self) -> None:
        """LoggingProgressCallback doesn't raise."""
        callback = LoggingProgressCallback(log_interval=1)
        callback.on_progress(50, 100, 50.0)
        callback.on_file_change("test.txt")
        callback.on_stats_update(IngestionStats(created=5))


class TestHookRegistry:
    """Tests for ingestion hooks."""

    def setup_method(self) -> None:
        """Clear hooks before each test."""
        get_hook_registry().clear()

    def test_singleton_pattern(self) -> None:
        """HookRegistry is a singleton."""
        r1 = HookRegistry()
        r2 = HookRegistry()
        assert r1 is r2

    def test_register_hook(self) -> None:
        """Can register a hook."""
        registry = get_hook_registry()
        hook = LoggingHook()
        registry.register(hook)
        assert hook in registry.get_all()

    def test_unregister_hook(self) -> None:
        """Can unregister a hook."""
        registry = get_hook_registry()
        hook = LoggingHook()
        registry.register(hook)
        registry.unregister(hook)
        assert hook not in registry.get_all()

    def test_notify_does_not_raise(self) -> None:
        """Hook notifications don't raise on hook errors."""
        registry = get_hook_registry()

        class FailingHook:
            def on_entity_created(self, entity_type: Any, entity: Any) -> None:
                raise RuntimeError("Hook failed")

            def on_batch_complete(self, entities: Any) -> None:
                raise RuntimeError("Hook failed")

            def on_import_complete(self, import_run: Any) -> None:
                raise RuntimeError("Hook failed")

        registry.register(FailingHook())

        # These should not raise - passing None to test error handling
        registry.notify_entity_created(EntityType.MEDIA, None)  # type: ignore[arg-type]
        registry.notify_batch_complete({})
        registry.notify_import_complete(None)  # type: ignore[arg-type]


class TestDiscovery:
    """Tests for source discovery."""

    def setup_method(self) -> None:
        """Clear registry before each test."""
        get_registry().clear()

    def test_discover_nonexistent_path(self) -> None:
        """discover() raises for nonexistent paths."""
        with pytest.raises(FileNotFoundError):
            discover(Path("/nonexistent/path"))

    def test_discover_empty_directory(self) -> None:
        """discover() handles empty directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = discover(Path(tmpdir))
            assert result.is_generic
            assert not result.has_content

    def test_discover_directory_with_images(self) -> None:
        """discover() finds image files in directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Create some dummy image files
            (tmppath / "photo1.jpg").write_bytes(b"fake jpeg")
            (tmppath / "photo2.png").write_bytes(b"fake png")

            result = discover(tmppath)
            assert result.is_generic
            assert result.has_content
            assert EntityType.MEDIA in result.available_entities
            assert result.available_entities[EntityType.MEDIA] == 2

    def test_discover_with_mixed_content(self) -> None:
        """discover() finds mixed content types."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "image.jpg").write_bytes(b"fake")
            (tmppath / "notes.md").write_text("# Notes")

            result = discover(tmppath)
            assert result.is_generic
            assert EntityType.MEDIA in result.available_entities
            assert EntityType.KNOWLEDGE_NOTE in result.available_entities
