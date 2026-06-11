"""Content hashing utilities for dedup and file integrity."""

import hashlib
import unicodedata
from pathlib import Path
from typing import Final

from potluck.models.drafts import BaseDraft

_CHUNK: Final[int] = 1024 * 1024  # 1 MiB


def content_hash(draft: BaseDraft) -> str:
    """sha256 hex over the NFC-normalized canonical identity of a draft.

    Identity = kind, external_id, ts(ISO), title, text, lat, lon. Each part is
    NFC-normalized and length-prefixed (``{len}:{part}``) — an injective
    encoding, so a separator byte inside a value can never shift content
    across field boundaries and collide.
    meta is deliberately EXCLUDED: parser-version cosmetics must not defeat dedup.
    """
    parts = [
        draft.kind.value,
        draft.external_id or "",
        draft.ts.isoformat() if draft.ts is not None else "",
        draft.title or "",
        draft.text or "",
        repr(draft.lat) if draft.lat is not None else "",
        repr(draft.lon) if draft.lon is not None else "",
    ]
    normalized = [unicodedata.normalize("NFC", part) for part in parts]
    raw = "".join(f"{len(part)}:{part}" for part in normalized).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_hash(path: Path) -> str:
    """Chunked sha256 of a file (1 MiB chunks) — for the imports ledger."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()
