"""Takeout Google Chat source plugin.

Parses the per-group JSON inside Google Takeout's Google Chat folder. Format
spec (v1 authoritative; layout, field names, nesting, and every claim below
verified against a real 2025-12 Takeout, shape only; v0's chat ingester read
the same files and its semantics port: non-content records are skipped,
attachments become metadata-only file references, DM/Space discrimination
comes from the group directory prefix):

- Layout: ``Takeout/Google Chat/Groups/<group>/`` holds one directory per
  conversation — ``DM <opaque-id>`` for direct messages (group DMs
  included), ``Space <opaque-id>`` for named rooms — each containing
  ``group_info.json`` + ``messages.json`` plus ``File-*`` attachment blobs.
  ``Takeout/Google Chat/Users/User <id>/`` carries ``user_info.json`` (the
  export owner: ``user.name/email/user_type`` + ``membership_info``) and
  ``unsentmessages.json`` (drafts UI state, never items).
- ``group_info.json``: ``members`` = list of ``{name, email, user_type}``;
  spaces additionally carry the room ``name`` (no DM of the real export
  has one).
- ``messages.json``: ``{"messages": [...]}``; every record of the real
  export carries ``creator {name, email, user_type}``, ``created_date``,
  ``topic_id``, and ``message_id``; ``text`` is absent exactly on
  attachment-only records (``attached_files`` = list of
  ``{export_name, original_name}``); span ``annotations`` (url previews,
  youtube_metadata, video_call_metadata) decorate offsets INSIDE ``text``
  and are dropped — the content they annotate survives verbatim in the
  text itself. Records with neither text nor usable attachments
  (membership/system stubs — the real export contains none and the shape
  has no discriminator field, so "nothing to index" is the robust rule)
  are skipped silently, the whatsapp system-message posture.

Sidecar coordination (first source to do it): ``group_info.json`` cannot be
parsed inline — the real archive interleaves it both BEFORE and AFTER its
sibling ``messages.json`` — so parse() makes two sequential passes: pass 1
collects the tiny sidecars (every ``group_info.json`` plus the owner email
from ``user_info.json``), pass 2 streams the messages members and joins by
group directory. Memory stays bounded by the sidecar summaries (a few
hundred bytes per conversation), never by message volume, and a multi-part
set that splits a group's files across parts joins correctly because each
pass chains all parts.

Kind mapping: chat messages → ``kind=message`` through the existing
messages satellite (migration 011 — no new schema). ``chat_key`` is the
group DIRECTORY name (``DM <id>`` / ``Space <id>``): the opaque id is
Google's backend group resource id — the first segment of every
``message_id`` repeats it — so it is stable across re-exports, and the
prefix doubles as the DM-vs-space discriminator. ``chat_name``: a space's
room name; for DMs (and unnamed spaces) the ``, ``-joined display names of
every member OTHER than the export owner (emails compared
case-insensitively), falling back to all members when no ``user_info.json``
named the owner, and to None without a sidecar at all. ``sender`` is the
creator display name exactly as exported (email when the name is missing);
the creator email rides ``meta.sender_email`` — the user's own local chat
data, the gmail address-storage posture. Reactions do not appear in the
real export and are not read.

Identity policy: ``gchat:<message_id>`` — message ids are native
identities (``<group-id>/<topic-id>/<message-id>``, globally unique across
all 1,644 real messages, server-side resource ids so re-exports repeat
them). A defensive first-seen ``#N`` suffix covers duplicated ids, with
occurrence counters scoped PER MEMBER: two members carrying one group are
re-exports of the same chat, so their copies must collide on external_id
and dedup rather than double-import (the chrome/ynab posture). A record
without a message_id (foreign shape, never seen in the real export) warns
and falls back to ``gchat:<sha256(...)>`` over VERBATIM exported values
(chat_key, raw created_date string, creator, text, attachment names) —
never parsed/cleaned derivatives, so parser evolution cannot re-mint
identities (P2 finding 6); the fallback form contains no ``/`` and can
never collide with a real three-segment id.

Timestamps: ``created_date`` renders as
``"Thursday, March 14, 2024 at 10:30:15 PM UTC"`` — full English names,
non-padded day/hour, a NARROW NO-BREAK SPACE (U+202F) before the meridiem
(on EVERY real message — the same invisible character WhatsApp's iOS
exporter uses), and UTC throughout. Parsing normalizes U+202F/U+00A0 to
plain spaces then applies a hand-rolled English-month regex
(``strptime %A/%B`` match locale-dependent names and would break on any
non-C machine locale); a
non-``UTC`` zone token is taken AS UTC — the whatsapp/gmail unknown-zone
policy — with one WARNING per member naming the token. An unparseable or
missing created_date keeps the message undated (identity does not need it
— the calendar posture) and warns once per member.

Containment: a member whose JSON is malformed logs one WARNING and is
skipped (drafts already yielded stand); a top level without a ``messages``
array logs one WARNING and yields nothing (a foreign shape must never
import as zero items silently); an empty ``messages`` array is a
legitimate state (three of the real export's ten DMs) and stays silent; a
non-object record warns and is skipped; a malformed sidecar warns and
degrades (chat_name None / owner unknown) without losing messages.

Detection is anchored on the ``Google Chat/Groups/`` parent segments plus
the exact ``messages.json`` basename — ``group_info.json``, the Users/
members (``user_info.json``, ``unsentmessages.json`` — which merely ENDS
in ``messages.json``), bare ``messages.json``, and other products' files
never match. Consequence: a hand-extracted lone group directory without
its ``Google Chat/Groups/`` parents is deliberately not detected.
"""

import hashlib
import json
import logging
import mimetypes
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from pydantic import JsonValue

from potluck.ingest.identity import occurrence_suffix
from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import MessageDraft, MessageMedia
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)

# '*/' (with '*' crossing '/') covers the standard Takeout/ nesting and
# re-zipped deeper layouts, the bare alternatives a root-relative
# Google Chat/ folder. The exact basename keeps unsentmessages.json (and
# every sidecar) out.
_EXPORT_GLOB = Glob("Google Chat/Groups/*/messages.json|*/Google Chat/Groups/*/messages.json")
_GROUP_INFO_GLOB = Glob(
    "Google Chat/Groups/*/group_info.json|*/Google Chat/Groups/*/group_info.json"
)
_USER_INFO_GLOB = Glob("Google Chat/Users/*/user_info.json|*/Google Chat/Users/*/user_info.json")

# The real created_date shape (module docstring). The weekday is decorative
# and ignored; the zone token is captured for the non-UTC warning.
_CREATED_RE: Final = re.compile(
    r"^[A-Za-z]+, (?P<month>[A-Za-z]+) (?P<day>\d{1,2}), (?P<year>\d{4}) at "
    r"(?P<hh>\d{1,2}):(?P<mm>\d{2}):(?P<ss>\d{2}) (?P<ampm>[AP]M) (?P<tz>\S+)$"
)
_MONTHS: Final = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}


@dataclass(frozen=True, slots=True)
class _GroupInfo:
    """One parsed group_info.json sidecar: room name + (name, email) members."""

    name: str | None
    members: tuple[tuple[str | None, str | None], ...]


@dataclass(slots=True)
class _MemberFlags:
    """Once-per-member warning latches (date shape, non-UTC zone token)."""

    date_warned: bool = False
    tz_warned: bool = False


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _group_dir(member_name: str) -> str:
    """The group directory (chat_key): the member's immediate parent segment.

    Detection and sidecar globs both guarantee a parent, and both files of
    one group share it — the join key of the two passes.
    """
    return member_name.split("/")[-2]


def _parse_created_date(raw: str) -> tuple[datetime, str] | None:
    """Parse the real created_date shape to (aware UTC instant, zone token).

    None for any foreign shape, unknown month name, or impossible date. The
    token is always APPLIED as UTC (module docstring); the caller owns the
    non-UTC warning. The narrow/no-break spaces the exporter mixes in
    (U+202F before the meridiem, on every real message) normalize to plain
    spaces before matching.
    """
    m = _CREATED_RE.match(raw.replace("\u202f", " ").replace("\u00a0", " "))
    if m is None:
        return None
    month = _MONTHS.get(m["month"])
    if month is None:
        return None
    hour = int(m["hh"]) % 12
    if m["ampm"] == "PM":
        hour += 12
    try:
        instant = datetime(
            int(m["year"]), month, int(m["day"]), hour, int(m["mm"]), int(m["ss"]), tzinfo=UTC
        )
    except ValueError:  # impossible dates (February 31)
        return None
    return instant, m["tz"]


def _parse_group_info(data: bytes, member_name: str) -> _GroupInfo:
    """Parse one group_info.json; malformed input warns and degrades to an
    empty sidecar (messages still import, just unnamed)."""
    try:
        doc: object = json.loads(data.decode("utf-8-sig", errors="replace"))
    except json.JSONDecodeError as exc:
        _logger.warning("google_chat: unreadable group_info %r: %s", member_name, exc)
        return _GroupInfo(name=None, members=())
    if not isinstance(doc, dict):
        _logger.warning("google_chat: group_info %r is not an object", member_name)
        return _GroupInfo(name=None, members=())
    members: list[tuple[str | None, str | None]] = []
    raw_members: object = doc.get("members")
    if isinstance(raw_members, list):
        for entry in raw_members:
            if not isinstance(entry, dict):
                continue
            name, email = _str_or_none(entry.get("name")), _str_or_none(entry.get("email"))
            if name is not None or email is not None:
                members.append((name, email))
    return _GroupInfo(name=_str_or_none(doc.get("name")), members=tuple(members))


def _parse_user_info(data: bytes, member_name: str) -> str | None:
    """The export owner's email from one user_info.json, or None (warned)."""
    try:
        doc: object = json.loads(data.decode("utf-8-sig", errors="replace"))
    except json.JSONDecodeError as exc:
        _logger.warning("google_chat: unreadable user_info %r: %s", member_name, exc)
        return None
    user: object = doc.get("user") if isinstance(doc, dict) else None
    email = _str_or_none(user.get("email")) if isinstance(user, dict) else None
    if email is None:
        _logger.warning(
            "google_chat: user_info %r carries no owner email — DM names will "
            "include every participant",
            member_name,
        )
    return email


def _chat_name(info: _GroupInfo | None, owner_email: str | None) -> str | None:
    """Human chat title: room name, else the other participants' names.

    The owner is excluded by case-insensitive email match when known;
    without an owner every member joins (better a self-including title than
    none). Members without a name fall back to their email. None when no
    sidecar (or no usable member) exists.
    """
    if info is None:
        return None
    if info.name is not None:
        return info.name
    owner = owner_email.lower() if owner_email is not None else None
    others = [
        name or email
        for name, email in info.members
        if not (owner is not None and email is not None and email.lower() == owner)
    ]
    return ", ".join(n for n in others if n) or None


def _media(record: dict[str, object]) -> tuple[MessageMedia, ...]:
    """attached_files → metadata-only media references. export_name (the
    ``File-*`` archive member basename) is preferred over original_name: it
    locates the blob for P6 pixel ingestion; the two differ only by that
    prefix and Takeout's ``(N)`` dedup suffixes."""
    raw: object = record.get("attached_files")
    if not isinstance(raw, list):
        return ()
    media: list[MessageMedia] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        filename = _str_or_none(entry.get("export_name")) or _str_or_none(
            entry.get("original_name")
        )
        if filename is None:
            continue
        media.append(MessageMedia(filename=filename, mime=mimetypes.guess_type(filename)[0]))
    return tuple(media)


def _fallback_id(
    chat_key: str,
    raw_date: str | None,
    name: str | None,
    email: str | None,
    text: str | None,
    media: tuple[MessageMedia, ...],
) -> str:
    """Composite identity for a record without a message_id: sha256 over
    verbatim exported values (module docstring). \\x1f separates fields,
    \\x1d entries within the attachment list, so values can never shift
    across boundaries."""
    parts = (
        chat_key,
        raw_date or "",
        name or "",
        email or "",
        text or "",
        "\x1d".join(m.filename for m in media),
    )
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8", errors="replace")).hexdigest()
    return f"gchat:{digest}"


def _build_draft(
    record: object,
    ordinal: int,
    member_name: str,
    chat_key: str,
    chat_name: str | None,
    counters: dict[str, int],
    flags: _MemberFlags,
) -> MessageDraft | None:
    """Assemble one record into a draft; None = skipped (non-content is
    silent, malformed shapes warn)."""
    if not isinstance(record, dict):
        _logger.warning(
            "google_chat: record %d in %r is not an object — skipped", ordinal, member_name
        )
        return None

    text = _str_or_none(record.get("text"))
    media = _media(record)
    if text is None and not media:
        return None  # membership/system stub: nothing to index, never an item

    creator: object = record.get("creator")
    if not isinstance(creator, dict):
        creator = {}
    name = _str_or_none(creator.get("name"))
    email = _str_or_none(creator.get("email"))

    ts: datetime | None = None
    raw_date = _str_or_none(record.get("created_date"))
    parsed = _parse_created_date(raw_date) if raw_date is not None else None
    if parsed is None:
        if not flags.date_warned:
            _logger.warning(
                "google_chat: record %d in %r has no recognizable created_date "
                "— stored without timestamp (further occurrences in this "
                "member are counted silently)",
                ordinal,
                member_name,
            )
            flags.date_warned = True
    else:
        ts, tz = parsed
        if tz != "UTC" and not flags.tz_warned:
            _logger.warning(
                "google_chat: %r renders timestamps in %s — read as UTC "
                "(unknown-zone policy; warned once per member)",
                member_name,
                tz,
            )
            flags.tz_warned = True

    message_id = _str_or_none(record.get("message_id"))
    if message_id is not None:
        base = f"gchat:{message_id}"
    else:
        _logger.warning(
            "google_chat: record %d in %r has no message_id — composite "
            "fallback identity from the exported values",
            ordinal,
            member_name,
        )
        base = _fallback_id(chat_key, raw_date, name, email, text, media)
    suffix = occurrence_suffix(counters, base)

    meta: dict[str, JsonValue] = {"sender_email": email} if email is not None else {}
    return MessageDraft(
        external_id=base + suffix,
        ts=ts,
        text=text,
        meta=meta,
        chat_key=chat_key,
        chat_name=chat_name,
        sender=name or email,
        is_media=bool(media),
        media=media,
    )


def _parse_messages(
    data: bytes, member_name: str, chat_key: str, chat_name: str | None
) -> Iterator[MessageDraft]:
    """Yield drafts from one messages.json member.

    JSON discipline: utf-8 with BOM tolerance, undecodable bytes replaced; a
    JSON error logs one WARNING and skips the member; a top level without a
    ``messages`` array logs one WARNING and yields nothing; an empty array
    is a legitimate empty conversation and stays silent. Occurrence counters
    are member-scoped (module docstring: re-export copies must dedup)."""
    try:
        doc: object = json.loads(data.decode("utf-8-sig", errors="replace"))
    except json.JSONDecodeError as exc:
        _logger.warning("google_chat: JSON error in %r: %s — member skipped", member_name, exc)
        return
    records: object = doc.get("messages") if isinstance(doc, dict) else None
    if not isinstance(records, list):
        _logger.warning('google_chat: %r has no "messages" array — member skipped', member_name)
        return
    counters: dict[str, int] = {}
    flags = _MemberFlags()
    for ordinal, record in enumerate(records, start=1):
        draft = _build_draft(record, ordinal, member_name, chat_key, chat_name, counters, flags)
        if draft is not None:
            yield draft


@source(
    name="google_chat",
    detect=_EXPORT_GLOB,
    kinds=(ItemKind.MESSAGE,),
    parser_version=1,
)
def parse(archive: Archive, ctx: ParseContext) -> Iterator[MessageDraft]:
    """Yield MessageDrafts from every group, two streaming passes.

    Pass 1 collects the sidecars (group_info.json summaries + the owner
    email from the first user_info.json that names one); pass 2 streams the
    messages members. Two passes because the real archive interleaves
    sidecars on both sides of their messages.json (module docstring) — the
    alternative, buffering messages until their sidecar arrives, is
    unbounded. Attachment blobs and every unmatched member are skipped
    unopened. ctx is part of the plugin contract but unused: there is
    nothing to parallelize.
    """
    groups: dict[str, _GroupInfo] = {}
    owner_email: str | None = None
    for member, stream in archive.iter_members("*info.json"):
        if _GROUP_INFO_GLOB.matches(member.name):
            groups[_group_dir(member.name)] = _parse_group_info(stream.read(), member.name)
        elif _USER_INFO_GLOB.matches(member.name) and owner_email is None:
            owner_email = _parse_user_info(stream.read(), member.name)
    for member, stream in archive.iter_members("*/messages.json"):
        if not _EXPORT_GLOB.matches(member.name):
            continue
        chat_key = _group_dir(member.name)
        chat_name = _chat_name(groups.get(chat_key), owner_email)
        yield from _parse_messages(stream.read(), member.name, chat_key, chat_name)
