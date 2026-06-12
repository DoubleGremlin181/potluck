"""Opaque keyset cursors for search pagination (#128).

Payload: ``v1:<max_id>:<score-hex>:<last_id>``, urlsafe-base64. The score is
hex-packed IEEE-754 (struct '>d') for EXACT round-trips — repr/json floats
would corrupt the keyset comparison. max_id freezes the candidate set at the
first page, so concurrent inserts (always higher rowids) can never shift
pagination; last_score is only the fallback ordering anchor — the service
re-resolves the anchor document's CURRENT score before paging, which keeps
continuation exact even when corpus statistics drift re-scores documents.
"""

import base64
import binascii
import struct
from dataclasses import dataclass

from potluck.core.errors import InvalidCursorError

_VERSION = "v1"


@dataclass(frozen=True)
class SearchCursor:
    """Decoded pagination state: resume AFTER (last_score, last_id) within
    the candidate set frozen at max_id."""

    max_id: int
    last_score: float
    last_id: int


def encode_cursor(*, max_id: int, last_score: float, last_id: int) -> str:
    score_hex = struct.pack(">d", last_score).hex()
    payload = f"{_VERSION}:{max_id}:{score_hex}:{last_id}"
    return base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii")


def decode_cursor(raw: str) -> SearchCursor:
    """Parse a cursor produced by encode_cursor.

    Raises InvalidCursorError on anything malformed — adapters surface it as
    a clean client error, never a stack trace.
    """
    try:
        payload = base64.urlsafe_b64decode(raw.encode("ascii")).decode("ascii")
        version, max_id, score_hex, last_id = payload.split(":")
        if version != _VERSION:
            raise ValueError(f"unsupported cursor version {version!r}")
        (last_score,) = struct.unpack(">d", bytes.fromhex(score_hex))
        return SearchCursor(max_id=int(max_id), last_score=last_score, last_id=int(last_id))
    except (ValueError, binascii.Error, struct.error, UnicodeError) as exc:
        raise InvalidCursorError(f"malformed search cursor: {raw!r}") from exc
