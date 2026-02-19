"""Document ingestion from text files, Obsidian vaults, and HTML files."""

import re
from collections.abc import Iterator
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.documents import Document
from potluck.pipeline.utils.hashing import compute_content_hash

logger = get_logger(__name__)

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".text", ".rst", ".html"}
MAX_FILE_SIZE = 1_000_000  # 1MB

# Directories to skip
SKIP_DIRS = {".obsidian", ".trash", ".git", ".svn", "__pycache__", "node_modules"}

# Regex for YAML front matter: starts and ends with ---
_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class _HTMLStripper(HTMLParser):
    """HTML parser that strips tags and extracts plain text."""

    def __init__(self) -> None:
        super().__init__()
        self._text = StringIO()

    def handle_data(self, data: str) -> None:
        self._text.write(data)

    def get_text(self) -> str:
        return self._text.getvalue()


def _strip_html_tags(html: str) -> str:
    """Strip HTML tags from content, returning plain text."""
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


def ingest_documents(
    path: Path,
    base_path: Path | None = None,
) -> Iterator[Document]:
    """Ingest text files as Document entities.

    Recursively scans for .txt, .md, .html, and other text files, with special
    handling for Obsidian vaults (skip .obsidian/ directory, parse YAML front
    matter) and HTML files (strip tags).

    Args:
        path: Path to scan (file or directory).
        base_path: Root path for computing relative source_id. Defaults to path.

    Yields:
        Document entities.
    """
    base_path = base_path or path

    if path.is_file():
        doc = _process_file(path, base_path)
        if doc:
            yield doc
        return

    logger.info(f"Processing text files at {path}")

    for file_path in sorted(path.rglob("*")):
        if not file_path.is_file():
            continue

        # Check extension
        if file_path.suffix.lower() not in TEXT_EXTENSIONS:
            continue

        # Skip files in hidden/excluded directories
        if _should_skip(file_path, base_path):
            continue

        doc = _process_file(file_path, base_path)
        if doc:
            yield doc


def is_obsidian_vault(path: Path) -> bool:
    """Check if a directory is an Obsidian vault."""
    return path.is_dir() and (path / ".obsidian").is_dir()


def count_document_files(path: Path) -> int:
    """Count text files in a directory (excluding hidden dirs)."""
    if path.is_file():
        return 1 if path.suffix.lower() in TEXT_EXTENSIONS else 0

    count = 0
    for file_path in path.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        if _should_skip(file_path, path):
            continue
        count += 1
    return count


def _should_skip(file_path: Path, base_path: Path) -> bool:
    """Check if a file should be skipped based on path rules."""
    try:
        rel_parts = file_path.relative_to(base_path).parts
    except ValueError:
        # Cannot compute relative path — don't skip
        return False

    return any(part.startswith(".") or part in SKIP_DIRS for part in rel_parts)


def _process_file(file_path: Path, base_path: Path) -> Document | None:
    """Process a single text file into a Document."""
    # Check file size
    try:
        file_size = file_path.stat().st_size
    except OSError as e:
        logger.warning(f"Could not stat {file_path}: {e}")
        return None

    if file_size == 0:
        return None
    if file_size > MAX_FILE_SIZE:
        logger.debug(f"Skipping large file ({file_size} bytes): {file_path}")
        return None

    # Read content
    content = _read_file(file_path)
    if not content:
        return None

    # Strip HTML tags for .html files, YAML front matter for markdown
    if file_path.suffix.lower() == ".html":
        content_body = _strip_html_tags(content)
    else:
        content_body = _strip_front_matter(content)

    if not content_body.strip():
        return None

    # Compute relative path for source_id
    try:
        relative_path = str(file_path.relative_to(base_path))
    except ValueError:
        relative_path = file_path.name

    return Document(
        source_type=SourceType.GENERIC,
        source_id=relative_path,
        content_hash=compute_content_hash(content_body),
        title=file_path.stem,
        content=content_body,
        file_extension=file_path.suffix.lower(),
    )


def _read_file(file_path: Path) -> str | None:
    """Read a text file with encoding fallback."""
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        logger.debug(f"UTF-8 decode failed for {file_path}, falling back to latin-1")
    except OSError as e:
        logger.warning(f"Failed to read {file_path}: {e}")
        return None
    try:
        return file_path.read_text(encoding="latin-1")
    except OSError as e:
        logger.warning(f"Failed to read {file_path}: {e}")
        return None


def _strip_front_matter(content: str) -> str:
    """Strip YAML front matter from content, returning just the body."""
    match = _FRONT_MATTER_RE.match(content)
    if match:
        return content[match.end() :]
    return content
