"""Lossy ingest-time text cleanup (#199).

Measured on a real 126k-message Takeout: 39.9% of all stored text chars were
unbroken tokens ≥120 chars (tracking URLs, base64 residue) plus 3M invisible
chars — pure index/storage junk. Cleaning at parse time shrinks items.text
and the FTS index by ~36%/26% and keeps snippets readable.

Cleaned text is what gets fingerprinted, hashed, stored, and indexed — any
change here is a gmail parser_version bump (content hashes move).
"""

import re
from typing import Final

# Zero-width and invisible characters: ZWSP, ZWNJ, ZWJ, WJ, BOM, SHY, CGJ.
_INVISIBLE: Final = dict.fromkeys(map(ord, "​‌‍⁠﻿­͏"))

# An unbroken run this long is junk (longest real words/URLs people search for
# are far shorter); keep the head so URL scheme+host stay searchable.
_RUN_THRESHOLD: Final = 120
_RUN_KEEP: Final = 80
_LONG_RUN: Final = re.compile(rf"\S{{{_RUN_THRESHOLD},}}")

# Sanity cap: the largest legitimate body observed is well under this; the
# one real outlier was a 2.4 MB pathological message.
MAX_TEXT_CHARS: Final = 256 * 1024


def clean_text(text: str) -> str:
    """Strip invisible chars, truncate junk runs, cap total size. Idempotent."""
    text = text.translate(_INVISIBLE)
    text = _LONG_RUN.sub(lambda m: m.group()[:_RUN_KEEP], text)
    return text[:MAX_TEXT_CHARS]
