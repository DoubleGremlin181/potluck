"""Chrome browser history and bookmarks ingestion from Google Takeout.

Handles:
- BrowserHistory.json: Chrome browsing history
- Bookmarks.html: Chrome bookmarks (Netscape HTML format)
"""

import contextlib
import hashlib
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.browsing import Bookmark, BookmarkFolder, BrowsingHistory
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.utils.parsers import parse_json

logger = get_logger(__name__)


def ingest_browsing_history(
    path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[BrowsingHistory]:
    """Ingest Chrome browsing history from Google Takeout.

    Parses BrowserHistory.json which contains an array of history entries.

    Args:
        path: Path to the extracted takeout directory.
        filters: Optional date range filters.

    Yields:
        BrowsingHistory entities.
    """
    chrome_dir = _find_chrome_dir(path)
    if not chrome_dir:
        logger.debug("No Chrome directory found in takeout")
        return

    history_file = chrome_dir / "BrowserHistory.json"
    if not history_file.exists():
        logger.debug("No BrowserHistory.json found")
        return

    logger.info(f"Processing Chrome history from {history_file}")

    try:
        data = parse_json(history_file)
    except Exception as e:
        logger.error(f"Failed to parse BrowserHistory.json: {e}")
        return

    if not isinstance(data, dict):
        logger.error("BrowserHistory.json is not a JSON object")
        return

    browser_history = data.get("Browser History", [])
    if not isinstance(browser_history, list):
        logger.error("Browser History is not an array")
        return

    yielded = 0
    skipped = 0

    for entry in browser_history:
        if not isinstance(entry, dict):
            skipped += 1
            continue

        try:
            entity = _parse_history_entry(entry)
            if entity is None:
                skipped += 1
                continue

            # Apply date filters
            if filters:
                if filters.since and entity.occurred_at and entity.occurred_at < filters.since:
                    skipped += 1
                    continue
                if filters.until and entity.occurred_at and entity.occurred_at >= filters.until:
                    skipped += 1
                    continue

            yield entity
            yielded += 1
        except Exception as e:
            logger.warning(f"Failed to parse history entry: {e}")
            skipped += 1

    logger.info(f"Processed {yielded} history entries, skipped {skipped}")


def _parse_history_entry(entry: dict[str, Any]) -> BrowsingHistory | None:
    """Parse a single browser history entry.

    Args:
        entry: History entry dictionary from BrowserHistory.json.

    Returns:
        BrowsingHistory entity or None if required fields missing.
    """
    url = entry.get("url")
    if not url:
        return None

    # Parse timestamp (time_usec is microseconds since epoch)
    time_usec = entry.get("time_usec", 0)
    occurred_at = None
    if time_usec:
        try:
            # Convert microseconds to seconds
            timestamp = time_usec / 1_000_000
            occurred_at = datetime.fromtimestamp(timestamp, tz=UTC)
        except (ValueError, OSError):
            pass

    # Extract domain from URL
    domain = _extract_domain(url)

    # Compute URL hash for deduplication
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:32]

    # Compute content hash for deduplication (url + timestamp)
    content = f"{url}|{time_usec}"
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    return BrowsingHistory(
        source_type=SourceType.GOOGLE_TAKEOUT,
        source_id=f"chrome-history-{url_hash}-{time_usec}",
        content_hash=content_hash,
        url=url,
        url_hash=url_hash,
        domain=domain,
        title=entry.get("title"),
        favicon_url=entry.get("favicon_url"),
        occurred_at=occurred_at,
        transition_type=entry.get("page_transition"),
        browser="Chrome",
    )


def ingest_bookmarks(
    path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[BookmarkFolder | Bookmark]:
    """Ingest Chrome bookmarks from Google Takeout.

    Parses Bookmarks.html (Netscape Bookmark HTML format).

    Args:
        path: Path to the extracted takeout directory.
        filters: Optional date range filters (applied to bookmarked_at).

    Yields:
        BookmarkFolder and Bookmark entities.
    """
    chrome_dir = _find_chrome_dir(path)
    if not chrome_dir:
        logger.debug("No Chrome directory found in takeout")
        return

    bookmarks_file = chrome_dir / "Bookmarks.html"
    if not bookmarks_file.exists():
        logger.debug("No Bookmarks.html found")
        return

    logger.info(f"Processing Chrome bookmarks from {bookmarks_file}")

    try:
        html_content = bookmarks_file.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.error(f"Failed to read Bookmarks.html: {e}")
        return

    # Parse bookmarks using custom HTML parser
    parser = _BookmarkHTMLParser()
    try:
        parser.feed(html_content)
    except Exception as e:
        logger.error(f"Failed to parse Bookmarks.html: {e}")
        return

    # Yield folders first, then bookmarks (to ensure folder IDs exist)
    yielded_folders = 0
    yielded_bookmarks = 0
    skipped = 0

    for folder in parser.folders:
        yield folder
        yielded_folders += 1

    for bookmark in parser.bookmarks:
        # Apply date filters
        if filters and bookmark.bookmarked_at:
            if filters.since and bookmark.bookmarked_at < filters.since:
                skipped += 1
                continue
            if filters.until and bookmark.bookmarked_at >= filters.until:
                skipped += 1
                continue

        yield bookmark
        yielded_bookmarks += 1

    logger.info(
        f"Processed {yielded_folders} folders, {yielded_bookmarks} bookmarks, skipped {skipped}"
    )


class _BookmarkHTMLParser(HTMLParser):
    """Parser for Netscape Bookmark HTML format.

    Netscape Bookmark format structure:
        <DL><p>
            <DT><H3 ADD_DATE="...">Folder Name</H3>
            <DL><p>
                <DT><A HREF="..." ADD_DATE="..." ICON_URI="...">Bookmark Title</A>
                ...
            </DL><p>
            <DT><A HREF="...">Bookmark Title</A>
            ...
        </DL>
    """

    def __init__(self) -> None:
        super().__init__()
        self.folders: list[BookmarkFolder] = []
        self.bookmarks: list[Bookmark] = []

        # Stack of (folder_id, folder_path) for nested folders
        self._folder_stack: list[tuple[UUID, str]] = []
        self._current_tag: str | None = None
        self._current_attrs: dict[str, str] = {}
        self._current_text: str = ""
        self._position: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Handle opening tags."""
        self._current_tag = tag.lower()
        self._current_attrs = {k.lower(): v or "" for k, v in attrs}
        self._current_text = ""

    def handle_data(self, data: str) -> None:
        """Handle text content."""
        self._current_text += data

    def handle_endtag(self, tag: str) -> None:
        """Handle closing tags."""
        tag = tag.lower()
        text = self._current_text.strip()

        if tag == "h3":
            # Folder header - create folder
            self._create_folder(text)
        elif tag == "a":
            # Bookmark link
            self._create_bookmark(text)
        elif tag == "dl" and self._folder_stack:
            # End of folder content - pop folder stack
            self._folder_stack.pop()

        self._current_tag = None
        self._current_attrs = {}
        self._current_text = ""

    def _create_folder(self, name: str) -> None:
        """Create a BookmarkFolder entity."""
        if not name:
            return

        folder_id = uuid4()
        parent_id: UUID | None = None
        parent_path = ""

        if self._folder_stack:
            parent_id, parent_path = self._folder_stack[-1]

        full_path = f"{parent_path}/{name}" if parent_path else name

        # Parse ADD_DATE attribute (Unix timestamp in seconds)
        folder_created_at = None
        add_date = self._current_attrs.get("add_date")
        if add_date:
            with contextlib.suppress(ValueError, OSError):
                folder_created_at = datetime.fromtimestamp(int(add_date), tz=UTC)

        folder = BookmarkFolder(
            id=folder_id,
            source_type=SourceType.GOOGLE_TAKEOUT,
            name=name,
            parent_id=parent_id,
            full_path=full_path,
            position=self._position,
            folder_created_at=folder_created_at,
        )

        self._position += 1
        self._folder_stack.append((folder_id, full_path))
        self.folders.append(folder)

    def _create_bookmark(self, title: str) -> None:
        """Create a Bookmark entity."""
        url = self._current_attrs.get("href")
        if not url:
            return

        folder_id: UUID | None = None
        folder_path: str | None = None

        if self._folder_stack:
            folder_id, folder_path = self._folder_stack[-1]

        # Parse ADD_DATE (Unix timestamp in seconds)
        bookmarked_at = None
        add_date = self._current_attrs.get("add_date")
        if add_date:
            with contextlib.suppress(ValueError, OSError):
                bookmarked_at = datetime.fromtimestamp(int(add_date), tz=UTC)

        # Extract domain
        domain = _extract_domain(url)

        # Compute URL hash
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:32]

        # Compute content hash (url is unique identifier)
        content_hash = hashlib.sha256(url.encode()).hexdigest()

        bookmark = Bookmark(
            source_type=SourceType.GOOGLE_TAKEOUT,
            source_id=f"chrome-bookmark-{url_hash}",
            content_hash=content_hash,
            url=url,
            url_hash=url_hash,
            domain=domain,
            title=title or None,
            icon_uri=self._current_attrs.get("icon_uri") or self._current_attrs.get("icon"),
            folder_id=folder_id,
            folder_path=folder_path,
            position=self._position,
            bookmarked_at=bookmarked_at,
        )

        self._position += 1
        self.bookmarks.append(bookmark)


def _find_chrome_dir(path: Path) -> Path | None:
    """Find Chrome directory in takeout."""
    candidates = [
        path / "Takeout" / "Chrome",
        path / "Chrome",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _extract_domain(url: str) -> str | None:
    """Extract domain from a URL.

    Args:
        url: Full URL string.

    Returns:
        Domain portion of the URL, or None if extraction fails.
    """
    # Simple regex to extract domain from URL
    match = re.match(r"https?://([^/]+)", url)
    if match:
        return match.group(1)
    return None
