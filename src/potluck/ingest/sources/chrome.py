"""Takeout Chrome browsing-history source plugin.

Parses the history JSON inside Google Takeout's Chrome folder. Format spec
(v1 authoritative; member name, top-level keys, and record field names
verified against a real 2025-12 Takeout — v0's Chrome ingester read the same
file and its semantics port: a record without a url is skipped, time_usec 0
means "no visit time", favicon_url is never a personal record):

- Member: ``Takeout/Chrome/History.json`` in current exports;
  ``BrowserHistory.json`` in older generations (both parsed identically —
  v0 documented the rename).
- Top level: one JSON object; ``"Browser History"`` holds the visit array.
  The sibling sections of the real export (``Typed Url``, ``Session``,
  ``Shared Tab Group``) are browser state, not personal records — decoded
  and discarded, never items.
- Record fields (all present on every record of the real export):
  ``favicon_url``, ``page_transition_qualifier``, ``title``, ``url``,
  ``time_usec`` (integer µs since the Unix epoch), ``client_id``. Older
  generations spell the transition ``page_transition``; both spellings pass
  through to meta under their exported names (never collapsed — v0's
  ``or``-merge lost information).

Kind mapping: browser history → ``kind=activity`` (the canonical vocabulary
maps "browser history, app usage" there). No satellite: ``title`` is the page
title (host+path of the url when the export's title is empty — 1.3% of real
records — so every visit stays human-readable); ``text`` is title + url, both
FTS-searchable (url only when the title is empty: the derived title would
just restate it); ``ts`` from time_usec via exact integer µs arithmetic
(float seconds would round the last digit at 2020s magnitudes); transition
fields and ``client_id`` ride meta; ``favicon_url`` is dropped (derivable
browser chrome, not record content).

Identity policy (visits carry no ids):
``chrome:<time_usec>:<first 16 hex of sha256(url)>`` with a first-seen ``#N``
suffix for byte-identical duplicates (same visit exported twice). The
composite is required: time alone collides across devices syncing into one
profile, url alone repeats on every revisit. Deliberately NOT the raw-block
fingerprint whatsapp/ynab use: JSON records have no stable raw serialization
across Takeout re-exports — field order and whitespace are serializer
accidents, so hashing raw bytes would re-mint every identity on a cosmetic
re-export. The composite hashes VERBATIM export values (the url string
exactly as exported, the integer time_usec), never parsed/cleaned
derivatives, so parser evolution cannot re-mint identities either — the same
property the raw-byte rule protects (P2 finding 6). Occurrence counters are
scoped PER MEMBER: two history members in one archive are re-exports of the
same history, so their copies must collide on external_id and dedup rather
than double-import (the ynab posture).

Memory at 200k-visit scale: a hand-rolled incremental walk over the decoded
text (json.JSONDecoder.raw_decode per top-level value, then per array
element) keeps peak parse memory at the member's text plus ONE record.
Measured on a synthetic 71 MB / 200k-record member: 153 MiB peak RSS vs
230 MiB for a whole-tree ``json.load`` — the tree's ~80 MiB grows linearly
with history size, the text does not. JSON errors mid-member log one WARNING
and stop that member (records already yielded stand); a member whose top
level lacks a ``"Browser History"`` array logs one WARNING and yields
nothing (a renamed/foreign shape must never import as zero items silently);
an empty visit array is a legitimate fresh-profile state and stays silent.

Detection is anchored on the ``Chrome/`` parent segment plus the two known
member names — bare ``History.json`` (a generic name, the #150 generic
ingesters' territory) never matches, and neither do the Chrome folder's
sibling members (Extensions.json, Settings.json, Dictionary.csv, …).
Consequence: a hand-extracted lone ``History.json`` without its Chrome/
folder is deliberately not detected.
"""

import hashlib
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlsplit

from pydantic import JsonValue

from potluck.ingest.identity import occurrence_suffix
from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import ActivityDraft
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)

# Both member-name generations, anchored on the Chrome/ parent segment so
# generic *History.json names can never match; '*/' (with '*' crossing '/')
# covers the standard Takeout/ nesting and re-zipped deeper layouts, the
# bare alternatives a root-relative Chrome/ folder.
_EXPORT_GLOB = Glob(
    "Chrome/History.json|*/Chrome/History.json"
    "|Chrome/BrowserHistory.json|*/Chrome/BrowserHistory.json"
)

_HISTORY_KEY: Final = "Browser History"

# Meta pass-through fields: both transition spellings (module docstring) and
# the device client id, verbatim under their exported names.
_META_FIELDS: Final = ("page_transition", "page_transition_qualifier", "client_id")

# 16 hex chars = 64 bits of url hash inside the composite identity — a
# collision needs two DIFFERENT urls visited at the SAME microsecond whose
# digests share 64 bits.
_URL_DIGEST_CHARS: Final = 16

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)

_DECODER: Final = json.JSONDecoder()
_WS: Final = " \t\n\r"


def _skip_ws(text: str, idx: int) -> int:
    while idx < len(text) and text[idx] in _WS:
        idx += 1
    return idx


def _iter_array(text: str, idx: int) -> Iterator[object]:
    """Yield the elements of the array starting at *idx* (which must point at
    ``[``), one at a time. Raises json.JSONDecodeError when malformed."""
    idx = _skip_ws(text, idx + 1)
    if idx < len(text) and text[idx] == "]":
        return
    while True:
        element, idx = _DECODER.raw_decode(text, idx)
        yield element
        idx = _skip_ws(text, idx)
        if idx < len(text) and text[idx] == ",":
            idx = _skip_ws(text, idx + 1)
            continue
        if idx < len(text) and text[idx] == "]":
            return
        raise json.JSONDecodeError("expected ',' or ']' in array", text, idx)


def _iter_history_records(text: str, member_name: str) -> Iterator[object]:
    """Yield the ``"Browser History"`` array's elements one at a time.

    Hand-rolled top-level walk so peak memory is the decoded text plus ONE
    record, never the whole tree (module docstring: measured evidence). Key
    order is not assumed; other top-level values are decoded and discarded —
    tiny in practice. Raises json.JSONDecodeError on malformed JSON
    (contained per member by the caller); a top level without the expected
    array logs one WARNING and yields nothing.
    """
    idx = _skip_ws(text, 0)
    if idx >= len(text) or text[idx] != "{":
        raise json.JSONDecodeError("expected a top-level object", text, idx)
    idx = _skip_ws(text, idx + 1)
    if idx < len(text) and text[idx] == "}":
        pass  # empty object → the missing-array warning below
    else:
        while True:
            key, idx = _DECODER.raw_decode(text, idx)
            idx = _skip_ws(text, idx)
            if idx >= len(text) or text[idx] != ":":
                raise json.JSONDecodeError("expected ':' after object key", text, idx)
            idx = _skip_ws(text, idx + 1)
            if key == _HISTORY_KEY and idx < len(text) and text[idx] == "[":
                yield from _iter_array(text, idx)
                return  # one history array per member; the rest is not read
            # Any other value — including a non-array under the history key
            # (foreign shape, warned below) — is decoded and discarded.
            _, idx = _DECODER.raw_decode(text, idx)
            idx = _skip_ws(text, idx)
            if idx < len(text) and text[idx] == ",":
                idx = _skip_ws(text, idx + 1)
                continue
            if idx < len(text) and text[idx] == "}":
                break
            raise json.JSONDecodeError("expected ',' or '}' in object", text, idx)
    _logger.warning('chrome: %r has no "Browser History" array — member skipped', member_name)


def _fallback_title(url: str) -> str:
    """host+path of *url* — a readable stand-in when the export carries no
    title (the exact url stays in text). The raw url survives a ValueError
    from urlsplit (malformed IPv6 brackets): the record must not be lost to
    a display nicety."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    return (parts.netloc + parts.path) or url


def _build_draft(
    record: object, member_name: str, ordinal: int, counters: dict[str, int]
) -> ActivityDraft | None:
    """Assemble one history record into a draft; None = skipped (warned)."""
    if not isinstance(record, dict):
        _logger.warning("chrome: record %d in %r is not an object — skipped", ordinal, member_name)
        return None

    url = record.get("url")
    if not isinstance(url, str) or not url:
        _logger.warning("chrome: record %d in %r has no url — skipped", ordinal, member_name)
        return None
    time_usec = record.get("time_usec")
    if isinstance(time_usec, bool) or not isinstance(time_usec, int):
        # The verified real shape is a plain integer; a foreign type must
        # never be guessed into an identity (bool is an int subclass).
        _logger.warning(
            "chrome: record %d in %r has no integer time_usec — skipped "
            "(identity needs the visit time)",
            ordinal,
            member_name,
        )
        return None

    ts: datetime | None = None
    if time_usec:  # 0 is Chrome's "no visit time", not the epoch instant
        try:
            ts = _EPOCH + timedelta(microseconds=time_usec)
        except OverflowError:
            # The id embeds the exported value either way — content must
            # survive an absurd timestamp.
            _logger.warning(
                "chrome: record %d in %r has out-of-range time_usec %d — stored without timestamp",
                ordinal,
                member_name,
                time_usec,
            )

    raw_title = record.get("title")
    exported_title = raw_title if isinstance(raw_title, str) and raw_title.strip() else None

    meta: dict[str, JsonValue] = {}
    for field in _META_FIELDS:
        value = record.get(field)
        if isinstance(value, str) and value:
            meta[field] = value

    digest = hashlib.sha256(url.encode("utf-8", errors="replace")).hexdigest()
    fingerprint = f"chrome:{time_usec}:{digest[:_URL_DIGEST_CHARS]}"
    suffix = occurrence_suffix(counters, fingerprint)

    return ActivityDraft(
        external_id=fingerprint + suffix,
        ts=ts,
        title=exported_title if exported_title is not None else _fallback_title(url),
        # url only when the title is empty — the derived title restates it.
        text=f"{exported_title}\n{url}" if exported_title is not None else url,
        meta=meta,
    )


def _parse_history(data: bytes, member_name: str) -> Iterator[ActivityDraft]:
    """Yield ActivityDrafts from one history member.

    JSON discipline: utf-8 with BOM tolerance, undecodable bytes replaced;
    a JSON error mid-member logs one WARNING and stops that member (records
    already yielded stand). Occurrence counters are member-scoped (module
    docstring: re-exports must dedup, not double-import).
    """
    text = data.decode("utf-8-sig", errors="replace")
    counters: dict[str, int] = {}
    ordinal = 0
    try:
        for record in _iter_history_records(text, member_name):
            ordinal += 1
            draft = _build_draft(record, member_name, ordinal, counters)
            if draft is not None:
                yield draft
    except json.JSONDecodeError as exc:
        _logger.warning(
            "chrome: JSON error in %r: %s — remaining records skipped", member_name, exc
        )


@source(
    name="chrome",
    detect=_EXPORT_GLOB,
    kinds=(ItemKind.ACTIVITY,),
    parser_version=1,
)
def parse(archive: Archive, ctx: ParseContext) -> Iterator[ActivityDraft]:
    """Yield ActivityDrafts from every history member, one streaming pass.

    A single ``*.json`` pattern pass keeps tar archives sequential; the
    Chrome folder's sibling members (Extensions.json, Settings.json, …) and
    every other product's JSON are skipped unopened. Per member the peak
    memory is the decoded text plus one record (module docstring). ctx is
    part of the plugin contract but unused: there is nothing to parallelize.
    """
    for member, stream in archive.iter_members("*.json"):
        if not _EXPORT_GLOB.matches(member.name):
            continue
        yield from _parse_history(stream.read(), member.name)
