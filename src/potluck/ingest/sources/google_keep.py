"""Google Keep source plugin.

Parses Takeout archives exported from Google Keep (``Takeout/Keep/*.json``).

Skip policy (authoritative spec; tested in tests/unit/ingest/sources/test_google_keep.py):

- ``isTrashed: true`` → note is skipped entirely.
- Text derivation:

  1. Use ``textContent`` if non-empty.
  2. Otherwise render ``listContent`` as a markdown checklist::

         - [x] checked item
         - [ ] unchecked item

     Empty-text list items are skipped.

- If both the derived text and the ``title`` are empty → skip (empty note).
- Malformed JSON or non-``dict`` root → log a WARNING and skip; one corrupt
  member must not abort a 10 000-note import.

Timestamp policy:

- Use ``userEditedTimestampUsec`` when truthy, else ``createdTimestampUsec``.
- Conversion: ``datetime(1970,1,1,UTC) + timedelta(microseconds=usec)``
  (avoids float division; preserves microsecond precision).
- Missing or 0 → ``ts=None`` (epoch-0 is treated as absent).

Meta policy — include ONLY present/meaningful fields:

- ``created``  → ``createdTimestampUsec`` as an ISO-8601 UTC string (same
  format family as ``items.ts``); omitted when 0/missing.  ``ts`` carries the
  last-edited instant, so this preserves the creation instant alongside it.
- ``labels``   → list of name strings.
- ``color``    → only when not ``"DEFAULT"``.
- ``isPinned`` → only when ``True``.
- ``isArchived`` → only when ``True``.
- ``annotations`` → list of ``{url, title, description}`` for WEBLINK entries.
- ``sharees``    → verbatim.
- ``attachments`` → verbatim.
- Unknown top-level keys (anything not in the documented key set) → copied
  verbatim into meta so future Keep fields are preserved transparently.
- Excluded: ``textContentHtml``, per-item ``textHtml`` (bulky; derivable).
"""

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from potluck.ingest.plugins import Glob, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import NoteDraft
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# All top-level keys in the documented Keep JSON schema.
# Keys NOT in this set are treated as "unknown future fields" and copied to meta.
_KNOWN_KEYS: frozenset[str] = frozenset(
    {
        "color",
        "isTrashed",
        "isPinned",
        "isArchived",
        "title",
        "userEditedTimestampUsec",
        "createdTimestampUsec",
        "textContent",
        "textContentHtml",
        "listContent",
        "labels",
        "sharees",
        "annotations",
        "attachments",
    }
)


def _usec_to_dt(usec: int) -> datetime:
    """Convert microseconds-since-epoch integer to timezone-aware UTC datetime."""
    return _EPOCH + timedelta(microseconds=usec)


def _to_draft(data: dict[str, Any], member_name: str) -> NoteDraft | None:
    """Convert one Keep JSON dict to a :class:`~potluck.models.drafts.NoteDraft`.

    Returns ``None`` when the note should be skipped (see module docstring for
    the full skip policy).
    """
    # --- Skip: trashed ---
    if data.get("isTrashed"):
        return None

    # --- Text derivation ---
    text_content: str = str(data.get("textContent") or "")
    if text_content:
        text: str | None = text_content
    else:
        list_content = data.get("listContent")
        if isinstance(list_content, list):
            lines: list[str] = []
            for item in list_content:
                if not isinstance(item, dict):
                    continue
                item_text = str(item.get("text") or "").strip()
                if not item_text:
                    continue
                mark = "x" if item.get("isChecked") else " "
                lines.append(f"- [{mark}] {item_text}")
            text = "\n".join(lines) if lines else None
        else:
            text = None

    # --- Title ---
    title_raw = data.get("title")
    title: str | None = str(title_raw) if title_raw else None

    # --- Skip: empty note (no text AND no title) ---
    if not text and not title:
        return None

    # --- Timestamp ---
    user_edited = data.get("userEditedTimestampUsec", 0)
    created = data.get("createdTimestampUsec", 0)
    usec_int = int(user_edited) if user_edited else (int(created) if created else 0)
    ts = _usec_to_dt(usec_int) if usec_int else None

    # --- Meta ---
    meta: dict[str, Any] = {}

    if created:
        meta["created"] = _usec_to_dt(int(created)).isoformat()

    labels = data.get("labels")
    if labels and isinstance(labels, list):
        meta["labels"] = [lbl["name"] for lbl in labels if isinstance(lbl, dict) and "name" in lbl]

    color = data.get("color", "DEFAULT")
    if color and color != "DEFAULT":
        meta["color"] = color

    if data.get("isPinned"):
        meta["isPinned"] = True

    if data.get("isArchived"):
        meta["isArchived"] = True

    annotations = data.get("annotations")
    if isinstance(annotations, list) and annotations:
        processed: list[dict[str, Any]] = []
        for ann in annotations:
            if not isinstance(ann, dict):
                continue
            if ann.get("source") != "WEBLINK":
                continue
            ann_entry: dict[str, Any] = {
                k: ann[k] for k in ("url", "title", "description") if k in ann
            }
            if ann_entry:
                processed.append(ann_entry)
        if processed:
            meta["annotations"] = processed

    sharees = data.get("sharees")
    if isinstance(sharees, list) and sharees:
        meta["sharees"] = sharees

    attachments = data.get("attachments")
    if isinstance(attachments, list) and attachments:
        meta["attachments"] = attachments

    # Unknown top-level keys → copy verbatim
    for key, value in data.items():
        if key not in _KNOWN_KEYS:
            meta[key] = value

    return NoteDraft(
        external_id=member_name,
        ts=ts,
        title=title,
        text=text,
        meta=meta,
    )


@source(name="google_keep", detect=Glob("*Keep/*.json"), kinds=(ItemKind.NOTE,), parser_version=2)
def parse(archive: Archive) -> Iterator[NoteDraft]:
    """Yield one :class:`~potluck.models.drafts.NoteDraft` per non-skipped Keep note."""
    for member, stream in archive.iter_members("*Keep/*.json"):
        try:
            raw: Any = json.loads(stream.read())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            _logger.warning(
                "google_keep: skipping malformed member %r: %s",
                member.name,
                exc,
            )
            continue
        if not isinstance(raw, dict):
            _logger.warning(
                "google_keep: skipping non-dict member %r (got %s)",
                member.name,
                type(raw).__name__,
            )
            continue
        draft = _to_draft(raw, member.name)
        if draft is not None:
            yield draft
