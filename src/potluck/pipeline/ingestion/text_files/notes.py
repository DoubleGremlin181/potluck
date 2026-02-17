"""Text file and Obsidian vault ingestion."""

import re
from collections.abc import Iterator
from pathlib import Path

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.notes import KnowledgeNote
from potluck.pipeline.utils.hashing import compute_content_hash

logger = get_logger(__name__)

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".text", ".rst"}
MAX_FILE_SIZE = 1_000_000  # 1MB

# Directories to skip
SKIP_DIRS = {".obsidian", ".trash", ".git", ".svn", "__pycache__", "node_modules"}

# Regex for YAML front matter: starts and ends with ---
_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def ingest_text_files(
    path: Path,
    base_path: Path | None = None,
) -> Iterator[KnowledgeNote]:
    """Ingest text files as KnowledgeNote entities.

    Recursively scans for .txt and .md files, with special handling for
    Obsidian vaults (skip .obsidian/ directory, parse YAML front matter).

    Args:
        path: Path to scan (file or directory).
        base_path: Root path for computing relative source_id. Defaults to path.

    Yields:
        KnowledgeNote entities.
    """
    base_path = base_path or path

    if path.is_file():
        note = _process_file(path, base_path)
        if note:
            yield note
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

        note = _process_file(file_path, base_path)
        if note:
            yield note


def is_obsidian_vault(path: Path) -> bool:
    """Check if a directory is an Obsidian vault."""
    return path.is_dir() and (path / ".obsidian").is_dir()


def count_text_files(path: Path) -> int:
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
        rel_parts = file_path.parts

    return any(part.startswith(".") or part in SKIP_DIRS for part in rel_parts)


def _process_file(file_path: Path, base_path: Path) -> KnowledgeNote | None:
    """Process a single text file into a KnowledgeNote."""
    # Check file size
    try:
        file_size = file_path.stat().st_size
    except OSError as e:
        logger.debug(f"Could not stat {file_path}: {e}")
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

    # Strip YAML front matter for the note content
    content_body = _strip_front_matter(content)
    if not content_body.strip():
        return None

    # Compute relative path for source_id
    try:
        relative_path = str(file_path.relative_to(base_path))
    except ValueError:
        relative_path = file_path.name

    return KnowledgeNote(
        source_type=SourceType.GENERIC,
        source_id=relative_path,
        content_hash=compute_content_hash(content_body),
        content=content_body,
        created_by="import",
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
