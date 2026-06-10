"""Content hashing utilities for dedup and file integrity."""

import hashlib
import unicodedata
from pathlib import Path
from typing import Final

from potluck.models.drafts import BaseDraft

_SEP: Final = "\x1f"
_CHUNK: Final[int] = 1024 * 1024  # 1 MiB


def content_hash(draft: BaseDraft) -> str:
    """sha256 hex over the NFC-normalized canonical identity of a draft.

    Identity = kind | external_id | ts(ISO) | title | text, joined by \\x1f.
    meta is deliberately EXCLUDED: parser-version cosmetics must not defeat dedup.
    """
    parts = [
        draft.kind.value,
        draft.external_id or "",
        draft.ts.isoformat() if draft.ts is not None else "",
        draft.title or "",
        draft.text or "",
    ]
    raw = unicodedata.normalize("NFC", _SEP.join(parts)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_hash(path: Path) -> str:
    """Chunked sha256 of a file (1 MiB chunks) — for the imports ledger."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()
