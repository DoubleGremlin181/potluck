"""Google Keep note ingestion from Google Takeout.

Handles:
- Keep/*.json: Individual note files

Each JSON file represents a single Google Keep note with metadata
like title, text/list content, labels, and timestamps.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from potluck.core.logging import get_logger
from potluck.models.base import SourceType
from potluck.models.documents import Document
from potluck.pipeline.dtos import PipelineFilter
from potluck.pipeline.utils.hashing import compute_content_hash

logger = get_logger(__name__)


def ingest_keep_notes(
    path: Path,
    filters: PipelineFilter | None = None,
) -> Iterator[Document]:
    """Ingest Google Keep notes from Takeout JSON files.

    Scans for JSON files in the Keep directory and creates Document
    entities. Both text notes and list/checklist notes are supported.

    Args:
        path: Path to the extracted takeout directory.
        filters: Optional date range filters.

    Yields:
        Document entities.
    """
    keep_dir = _find_keep_dir(path)
    if not keep_dir:
        logger.debug("No Google Keep directory found")
        return

    logger.info(f"Processing Google Keep at {keep_dir}")

    for json_file in sorted(keep_dir.glob("*.json")):
        if not json_file.is_file():
            continue

        doc = _process_keep_note(json_file, filters)
        if doc:
            yield doc


def count_keep_notes(path: Path) -> int:
    """Count Keep note JSON files in a directory."""
    keep_dir = _find_keep_dir(path)
    if not keep_dir:
        return 0
    return sum(1 for f in keep_dir.glob("*.json") if f.is_file())


def _find_keep_dir(path: Path) -> Path | None:
    """Find Google Keep directory in takeout."""
    candidates = [
        path / "Takeout" / "Keep",
        path / "Keep",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _process_keep_note(
    json_file: Path,
    filters: PipelineFilter | None,
) -> Document | None:
    """Process a single Google Keep JSON file into a Document entity."""
    try:
        raw = json_file.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"Failed to parse Keep note {json_file}: {e}")
        return None

    # Skip trashed notes
    if data.get("isTrashed", False):
        return None

    title = data.get("title") or None

    # Extract content: prefer textContent, fall back to formatted listContent
    content = data.get("textContent", "")
    if not content:
        list_items = data.get("listContent", [])
        if list_items:
            content = _format_list_content(list_items)

    if not content or not content.strip():
        return None

    # Parse timestamp for date filtering
    occurred_at = _parse_keep_timestamp(data.get("userEditedTimestampUsec"))
    if not occurred_at:
        occurred_at = _parse_keep_timestamp(data.get("createdTimestampUsec"))

    if filters and not filters.passes(occurred_at):
        return None

    return Document(
        source_type=SourceType.GOOGLE_TAKEOUT,
        source_id=f"keep:{json_file.name}",
        content_hash=compute_content_hash(content),
        title=title,
        content=content,
        file_extension=".json",
    )


def _format_list_content(items: list[dict[str, str | bool]]) -> str:
    """Format Google Keep list/checklist items as text.

    Args:
        items: List of {"text": str, "isChecked": bool} dicts.

    Returns:
        Formatted text with checkbox markers.
    """
    lines = []
    for item in items:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        checked = bool(item.get("isChecked", False))
        marker = "[x]" if checked else "[ ]"
        lines.append(f"{marker} {text}")
    return "\n".join(lines)


def _parse_keep_timestamp(timestamp_usec: int | str | None) -> datetime | None:
    """Parse a Google Keep timestamp (microseconds since epoch)."""
    if timestamp_usec is None:
        return None
    try:
        usec = int(timestamp_usec)
        return datetime.fromtimestamp(usec / 1_000_000, tz=UTC)
    except (ValueError, OSError, OverflowError):
        return None
