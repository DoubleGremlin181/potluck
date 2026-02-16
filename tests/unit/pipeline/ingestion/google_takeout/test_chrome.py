"""Tests for Chrome browser history and bookmarks ingestion."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from potluck.models.base import SourceType
from potluck.models.browsing import Bookmark, BookmarkFolder, BrowsingHistory
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.ingestion.google_takeout.chrome import (
    _extract_domain,
    ingest_bookmarks,
    ingest_browsing_history,
)


class TestBrowsingHistoryIngestion:
    """Tests for Chrome browsing history ingestion."""

    def test_ingest_history_from_fixtures(self, google_takeout_fixtures_path: Path) -> None:
        """Ingest history from fixture files."""
        entities: list[BrowsingHistory] = list(
            ingest_browsing_history(google_takeout_fixtures_path)
        )

        # Should have 5 history entries
        assert len(entities) == 5

        # All should be BrowsingHistory
        assert all(isinstance(e, BrowsingHistory) for e in entities)

        # Check first entry
        first = entities[0]
        assert first.url == "https://www.google.com/search?q=python"
        assert first.title == "Google Search"
        assert first.domain == "www.google.com"
        assert first.browser == "Chrome"
        assert first.source_type == SourceType.GOOGLE_TAKEOUT
        assert first.transition_type == "LINK"
        assert first.favicon_url == "https://www.google.com/favicon.ico"

    def test_ingest_history_parses_timestamps(self, google_takeout_fixtures_path: Path) -> None:
        """History entries have correct timestamps."""
        entities: list[BrowsingHistory] = list(
            ingest_browsing_history(google_takeout_fixtures_path)
        )

        # First entry: 1704067200000000 usec = 2024-01-01 00:00:00 UTC
        first = entities[0]
        assert first.occurred_at is not None
        assert first.occurred_at.year == 2024
        assert first.occurred_at.month == 1
        assert first.occurred_at.day == 1

    def test_ingest_history_with_date_filter_since(
        self, google_takeout_fixtures_path: Path
    ) -> None:
        """Date filter 'since' excludes earlier entries."""
        filters = PipelineFilter(since=datetime(2024, 1, 2, tzinfo=UTC))
        entities: list[BrowsingHistory] = list(
            ingest_browsing_history(google_takeout_fixtures_path, filters)
        )

        # Should exclude first entry (Jan 1) and last entry (2023)
        assert len(entities) == 3

        # All should be from Jan 2 or later
        for e in entities:
            assert e.occurred_at is not None
            assert filters.since is not None
            assert e.occurred_at >= filters.since

    def test_ingest_history_with_date_filter_until(
        self, google_takeout_fixtures_path: Path
    ) -> None:
        """Date filter 'until' excludes later entries."""
        filters = PipelineFilter(until=datetime(2024, 1, 3, tzinfo=UTC))
        entities: list[BrowsingHistory] = list(
            ingest_browsing_history(google_takeout_fixtures_path, filters)
        )

        # Should include entries before Jan 3 (Jan 1, Jan 2, and 2023 entry)
        assert len(entities) == 3

    def test_ingest_history_computes_hashes(self, google_takeout_fixtures_path: Path) -> None:
        """History entries have content and URL hashes."""
        entities: list[BrowsingHistory] = list(
            ingest_browsing_history(google_takeout_fixtures_path)
        )

        for e in entities:
            assert e.content_hash is not None
            assert len(e.content_hash) == 64  # SHA256 hex
            assert e.url_hash is not None
            assert len(e.url_hash) == 32  # Truncated SHA256

    def test_ingest_history_empty_directory(self) -> None:
        """Empty directory yields no entities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entities = list(ingest_browsing_history(Path(tmpdir)))
            assert entities == []

    def test_ingest_history_missing_file(self) -> None:
        """Missing BrowserHistory.json yields no entities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chrome_dir = Path(tmpdir) / "Chrome"
            chrome_dir.mkdir()
            entities = list(ingest_browsing_history(Path(tmpdir)))
            assert entities == []


class TestBookmarksIngestion:
    """Tests for Chrome bookmarks ingestion."""

    def test_ingest_bookmarks_from_fixtures(self, google_takeout_fixtures_path: Path) -> None:
        """Ingest bookmarks from fixture files."""
        entities = list(ingest_bookmarks(google_takeout_fixtures_path))

        # Separate folders and bookmarks
        folders = [e for e in entities if isinstance(e, BookmarkFolder)]
        bookmarks = [e for e in entities if isinstance(e, Bookmark)]

        # Should have 4 folders: Bookmark Bar, Development, Frameworks, Other Bookmarks
        assert len(folders) == 4

        # Should have 7 bookmarks
        assert len(bookmarks) == 7

    def test_ingest_bookmarks_folder_hierarchy(self, google_takeout_fixtures_path: Path) -> None:
        """Bookmarks folders have correct parent relationships."""
        entities = list(ingest_bookmarks(google_takeout_fixtures_path))
        folders = [e for e in entities if isinstance(e, BookmarkFolder)]

        # Find folders by name
        folder_by_name = {f.name: f for f in folders}

        # Bookmark Bar has no parent
        bookmark_bar = folder_by_name["Bookmark Bar"]
        assert bookmark_bar.parent_id is None
        assert bookmark_bar.full_path == "Bookmark Bar"

        # Development is under Bookmark Bar
        development = folder_by_name["Development"]
        assert development.parent_id == bookmark_bar.id
        assert development.full_path == "Bookmark Bar/Development"

        # Frameworks is under Development
        frameworks = folder_by_name["Frameworks"]
        assert frameworks.parent_id == development.id
        assert frameworks.full_path == "Bookmark Bar/Development/Frameworks"

    def test_ingest_bookmarks_folder_path(self, google_takeout_fixtures_path: Path) -> None:
        """Bookmarks have correct folder paths."""
        entities = list(ingest_bookmarks(google_takeout_fixtures_path))
        bookmarks = [e for e in entities if isinstance(e, Bookmark)]

        # Find bookmark by URL
        bookmark_by_url = {b.url: b for b in bookmarks}

        # FastAPI is in Development/Frameworks
        fastapi = bookmark_by_url["https://fastapi.tiangolo.com/"]
        assert fastapi.folder_path == "Bookmark Bar/Development/Frameworks"

        # Google is in root Bookmark Bar
        google = bookmark_by_url["https://www.google.com/"]
        assert google.folder_path == "Bookmark Bar"

    def test_ingest_bookmarks_parses_timestamps(self, google_takeout_fixtures_path: Path) -> None:
        """Bookmarks have correct ADD_DATE timestamps."""
        entities = list(ingest_bookmarks(google_takeout_fixtures_path))
        bookmarks = [e for e in entities if isinstance(e, Bookmark)]

        # Find Google bookmark
        google = next(b for b in bookmarks if "google.com" in b.url)

        # ADD_DATE="1704067200" = 2024-01-01 00:00:00 UTC
        assert google.bookmarked_at is not None
        assert google.bookmarked_at.year == 2024
        assert google.bookmarked_at.month == 1
        assert google.bookmarked_at.day == 1

    def test_ingest_bookmarks_parses_icon(self, google_takeout_fixtures_path: Path) -> None:
        """Bookmarks parse icon attributes."""
        entities = list(ingest_bookmarks(google_takeout_fixtures_path))
        bookmarks = [e for e in entities if isinstance(e, Bookmark)]

        # Find Google bookmark (has ICON attribute)
        google = next(b for b in bookmarks if "google.com" in b.url)
        assert google.icon_uri is not None
        assert google.icon_uri.startswith("data:image/png;base64,")

        # Find Python Docs (has ICON_URI attribute)
        python_docs = next(b for b in bookmarks if "docs.python.org" in b.url)
        assert python_docs.icon_uri == "https://docs.python.org/favicon.ico"

    def test_ingest_bookmarks_with_date_filter(self, google_takeout_fixtures_path: Path) -> None:
        """Date filter excludes bookmarks outside range."""
        filters = PipelineFilter(
            since=datetime(2024, 1, 1, tzinfo=UTC),
            until=datetime(2024, 1, 3, tzinfo=UTC),
        )
        entities = list(ingest_bookmarks(google_takeout_fixtures_path, filters))
        bookmarks = [e for e in entities if isinstance(e, Bookmark)]

        # Should exclude Hacker News (2023) and bookmarks after Jan 3
        # Within range: Google (Jan 1), GitHub (Jan 2)
        assert len(bookmarks) == 2

    def test_ingest_bookmarks_extracts_domain(self, google_takeout_fixtures_path: Path) -> None:
        """Bookmarks have extracted domain."""
        entities = list(ingest_bookmarks(google_takeout_fixtures_path))
        bookmarks = [e for e in entities if isinstance(e, Bookmark)]

        google = next(b for b in bookmarks if "google.com" in b.url)
        assert google.domain == "www.google.com"

        github = next(b for b in bookmarks if "github.com" in b.url)
        assert github.domain == "github.com"

    def test_ingest_bookmarks_computes_hashes(self, google_takeout_fixtures_path: Path) -> None:
        """Bookmarks have content and URL hashes."""
        entities = list(ingest_bookmarks(google_takeout_fixtures_path))
        bookmarks = [e for e in entities if isinstance(e, Bookmark)]

        for b in bookmarks:
            assert b.content_hash is not None
            assert len(b.content_hash) == 64  # SHA256 hex
            assert b.url_hash is not None

    def test_ingest_bookmarks_empty_directory(self) -> None:
        """Empty directory yields no entities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entities = list(ingest_bookmarks(Path(tmpdir)))
            assert entities == []


class TestExtractDomain:
    """Tests for domain extraction utility."""

    def test_extract_domain_https(self) -> None:
        """Extracts domain from HTTPS URLs."""
        assert _extract_domain("https://www.example.com/path") == "www.example.com"

    def test_extract_domain_http(self) -> None:
        """Extracts domain from HTTP URLs."""
        assert _extract_domain("http://example.org/") == "example.org"

    def test_extract_domain_with_port(self) -> None:
        """Extracts domain including port."""
        assert _extract_domain("https://localhost:8080/api") == "localhost:8080"

    def test_extract_domain_invalid(self) -> None:
        """Returns None for invalid URLs."""
        assert _extract_domain("not-a-url") is None
        assert _extract_domain("ftp://files.example.com") is None


class TestIntegrationWithStage:
    """Integration tests with GoogleTakeoutStage."""

    def test_stage_executes_chrome_ingestion(self, google_takeout_fixtures_path: Path) -> None:
        """Stage correctly routes to Chrome ingestion."""
        from potluck.models.base import EntityType
        from potluck.pipeline.ingestion.google_takeout import GoogleTakeoutStage

        stage = GoogleTakeoutStage()

        # Execute for browsing history only
        entities = list(
            stage.execute(
                google_takeout_fixtures_path,
                entity_types={EntityType.BROWSING_HISTORY},
            )
        )

        # Should get history entries
        assert len(entities) == 5
        assert all(isinstance(e, BrowsingHistory) for e in entities)

    def test_stage_executes_bookmarks_ingestion(self, google_takeout_fixtures_path: Path) -> None:
        """Stage correctly routes to bookmarks ingestion."""
        from potluck.models.base import EntityType
        from potluck.pipeline.ingestion.google_takeout import GoogleTakeoutStage

        stage = GoogleTakeoutStage()

        # Execute for bookmarks only
        entities = list(
            stage.execute(
                google_takeout_fixtures_path,
                entity_types={EntityType.BOOKMARK},
            )
        )

        # Should get folders and bookmarks (11 total)
        assert len(entities) == 11
