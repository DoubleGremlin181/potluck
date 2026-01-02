"""Tests for BrowsingHistory and Bookmark models."""

from potluck.models.base import SourceType
from potluck.models.browsing import Bookmark, BookmarkFolder, BrowsingHistory


class TestBrowsingModels:
    """Tests for BrowsingHistory and Bookmark models."""

    def test_browsing_history_creation(self) -> None:
        """BrowsingHistory can be created."""
        history = BrowsingHistory(
            source_type=SourceType.GOOGLE_TAKEOUT,
            url="https://example.com",
        )
        assert history.url == "https://example.com"
        assert history.visit_duration_seconds is None

    def test_bookmark_creation(self) -> None:
        """Bookmark can be created."""
        bookmark = Bookmark(
            source_type=SourceType.GOOGLE_TAKEOUT,
            url="https://example.com",
            title="Example Site",
        )
        assert bookmark.url == "https://example.com"
        assert bookmark.title == "Example Site"
        assert bookmark.is_favorite is False

    def test_bookmark_folder_creation(self) -> None:
        """BookmarkFolder can be created."""
        folder = BookmarkFolder(
            source_type=SourceType.GOOGLE_TAKEOUT,
            name="Tech",
        )
        assert folder.name == "Tech"
        assert folder.parent_id is None
