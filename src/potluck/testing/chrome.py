"""Deterministic synthetic Chrome-Takeout history generator.

Ships inside ``potluck.testing`` so tests, committed fixtures, and bench
scenarios share one deterministic source. Same arguments → identical bytes on
every machine, forever. Never put real personal data here — every url lives
under ``www.example.com``, titles come from the shared WORDS list.

Record shapes are modular rules of the record index ``i`` (not RNG draws), so
expected parser outcomes have exact closed forms. Per record ``i`` (first
rule wins):

- ``i % 25 == 24`` (i > 0) → verbatim copy of record ``i-1`` (same
  time_usec, same url, same bytes: exercises the parser's ``#N`` identity
  suffixes; see :func:`expected_duplicate_suffix_count`)
- ``i % 10 == 7``  → empty title (the host+path fallback path;
  :func:`expected_empty_title_count` — ~1.3% of real records are titleless)
- ``i % 9 == 4``   → unicode title (emoji + diacritics)
- ``i % 11 == 6``  → unicode url path with a query string
- otherwise        → plain WORDS-derived title and url

Timestamps (:func:`visit_time_usec`): a ~61 s prime-ish µs step from a fixed
base, so every visit lands on non-round microseconds (µs-precision
round-trips are always exercised), with a ~6.7 min back-step at ``i % 6 == 3``
so the array is non-monotonic like a real multi-device history — non-duplicate
records still never share a time_usec (the step never divides the dip).

The member set mirrors the real 2025-12 export: ``Takeout/Chrome/History.json``
(top-level sections ``Browser History`` / ``Typed Url`` / ``Session`` /
``Shared Tab Group``, record fields in the real order) beside sibling decoys
(Extensions.json, Settings.json, Dictionary.csv) the parser must never read.

Regenerate the golden fixture::

    python -c "
    from pathlib import Path
    from potluck.testing.chrome import write_chrome_takeout
    write_chrome_takeout(Path('tests/fixtures/chrome'), 60, seed=11, fmt='dir')
    "
"""

import json
from pathlib import Path
from typing import Literal

from potluck.testing.archives import write_archive
from potluck.testing.generators import WORDS

_BASE_USEC = 1_683_793_800_000_000  # 2023-05-11T08:30:00Z
_STEP_USEC = 61_000_037  # ~61 s, never a whole second → non-round µs
_DIP_USEC = 400_000_000  # ~6.7 min back-step (out-of-order shape)

# Public Chrome page-transition constants — verbatim pass-through values.
_TRANSITIONS = ("LINK", "TYPED", "RELOAD", "GENERATED", "FORM_SUBMIT")
_CLIENT_IDS = ("c3ludGgtY2xpZW50LUE=", "c3ludGgtY2xpZW50LUI=")
_UNICODE_TITLE_TAILS = ("🎉 Zürich", "🚀 café", "✨ København")

_HISTORY_MEMBER = "Takeout/Chrome/History.json"

_DECOYS: dict[str, bytes] = {
    "Takeout/Chrome/Extensions.json": (
        b'{"Extensions": [{"name": "Synthetic Decoy Extension", "id": "aaaabbbbcccc"}]}'
    ),
    "Takeout/Chrome/Settings.json": b'{"Settings": [{"name": "synthetic", "value": "decoy"}]}',
    "Takeout/Chrome/Dictionary.csv": b"decoyword\nsynthword\n",
}


def _is_dup(i: int) -> bool:
    return i > 0 and i % 25 == 24  # i-1 is never itself a dup (i-1 % 25 == 23)


def _effective(i: int) -> int:
    return i - 1 if _is_dup(i) else i


def visit_time_usec(i: int) -> int:
    """time_usec of logical record *i* (dup records repeat record ``i-1``;
    resolve with ``i-1`` first). Pure int arithmetic — the parser must
    reproduce the instant exactly, to the microsecond."""
    usec = _BASE_USEC + i * _STEP_USEC
    if i % 6 == 3:
        usec -= _DIP_USEC
    return usec


def expected_visit_count(count: int) -> int:
    """Items the parser yields for one generated history of *count* records
    (every record imports — duplicates via their ``#N`` suffixes)."""
    return count


def expected_duplicate_suffix_count(count: int) -> int:
    """Records that import with a ``#N`` external-id suffix (verbatim dups)."""
    return sum(1 for i in range(count) if _is_dup(i))


def expected_empty_title_count(count: int) -> int:
    """Records whose exported title is empty (the host+path fallback path)."""
    return sum(1 for i in range(count) if _effective(i) % 10 == 7)


def _words(salt: int, i: int, offset: int, k: int) -> str:
    return " ".join(WORDS[(salt + i * 7 + (offset + j) * 3) % len(WORDS)] for j in range(k))


def _visit_record(i: int, salt: int) -> dict[str, object]:
    """The exported record for logical index *i* (never called for dups),
    field names and order exactly as the real 2025-12 export."""
    if i % 10 == 7:
        title = ""
    elif i % 9 == 4:
        # i % 3 is constant on this residue class — rotate by i // 9 instead.
        title = f"{_words(salt, i, 0, 3)} {_UNICODE_TITLE_TAILS[(i // 9) % 3]}"
    else:
        title = _words(salt, i, 0, 3 + i % 4)

    if i % 11 == 6:
        url = f"https://www.example.com/wiki/Zürich_{i}?q=café"
    else:
        url = (
            f"https://www.example.com/{_words(salt, i, 30, 1)}"
            f"/{_words(salt, i, 50, 1)}-{i}?ref=r{i % 5}"
        )

    return {
        "favicon_url": f"https://www.example.com/favicons/{i % 7}.ico",
        "page_transition_qualifier": _TRANSITIONS[i % len(_TRANSITIONS)],
        "title": title,
        "url": url,
        "time_usec": visit_time_usec(i),
        "client_id": _CLIENT_IDS[i % len(_CLIENT_IDS)],
    }


def _history_bytes(count: int, salt: int) -> bytes:
    """One History.json member: records joined without building the whole
    document tree (the 200k bench corpus stays a cheap setup step)."""
    body = ",\n".join(
        json.dumps(_visit_record(_effective(i), salt), ensure_ascii=False) for i in range(count)
    )
    doc = (
        '{"Browser History": [\n' + body + "\n],\n"
        '"Typed Url": [],\n"Session": [],\n"Shared Tab Group": []}\n'
    )
    return doc.encode("utf-8")


def chrome_members(count: int, seed: int = 42) -> dict[str, bytes]:
    """The member set of one synthetic Takeout ({posix_name: content})."""
    salt = seed * 1009
    return {_HISTORY_MEMBER: _history_bytes(count, salt), **_DECOYS}


def write_chrome_takeout(
    dest_dir: Path,
    count: int,
    seed: int = 42,
    *,
    fmt: Literal["zip", "tgz", "dir"] = "zip",
) -> Path:
    """Materialise a synthetic Chrome Takeout archive in *dest_dir*.

    Returns the archive path (or the directory root for ``fmt="dir"``).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    members = chrome_members(count, seed)
    if fmt == "dir":
        dest = dest_dir / "chrome-synth-001"
        write_archive(dest, members, "dir")
        return dest
    ext = "zip" if fmt == "zip" else "tgz"
    dest = dest_dir / f"chrome-synth-001.{ext}"
    write_archive(dest, members, fmt)
    return dest
