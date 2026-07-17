"""WhatsApp chat-export (.txt) source plugin.

Parses the text files WhatsApp's "Export chat" produces — Android
("WhatsApp Chat with <name>.txt", optionally zipped with media) and iOS
("_chat.txt" inside "WhatsApp Chat - <name>.zip").

Format spec (v1 authoritative; v0's WhatsApp ingester read a decrypted
msgstore.db, so only its semantics port: system messages are skipped, media
become metadata-only file references with a guessed MIME type, senders are
stored exactly as exported):

- Android: ``M/D/YY, H:MM AM - Sender: text`` (12h US) or
  ``DD/MM/YYYY, HH:MM - Sender: text`` (24h); ``.`` and ``-`` also appear as
  date separators, and dotted/lowercase meridiems (``a.m.``) exist. Media:
  ``<Media omitted>`` (no-media export) or ``FILENAME (file attached)``
  (media export), with any caption on the continuation lines.
- iOS: ``[M/D/YY, H:MM:SS AM] Sender: text`` — seconds always present; lines
  may carry a leading LEFT-TO-RIGHT MARK and a NARROW NO-BREAK SPACE before
  AM/PM. Media: ``<attached: FILENAME>`` or ``image/video/... omitted``.
- System messages (encryption notices, joins, subject changes) have no
  ``Sender: `` segment and are skipped — export chrome, not items. iOS
  attributes the encryption notice to a sender; its body pattern is the one
  sender-attributed shape also skipped. Known ambiguity: a sender-less
  system line whose free text contains ``": "`` is indistinguishable from a
  message and comes through as one; the format gives no stronger anchor.
- Continuation lines (no timestamp prefix) belong to the previous message
  and concatenate with newlines. The converse ambiguity is inherent to the
  line-oriented format: a continuation line that itself matches the
  ``date, time - `` shape splits the message there, and a sender-less
  remainder is then dropped as system chrome. Non-English media/system
  placeholder texts are not recognised (they come through as plain
  messages); a detected chat file whose lines match NO timestamp dialect at
  all yields zero drafts and logs one WARNING naming the member — never a
  silent empty import.

Locale policy: day/month order is inferred once per file — the first date
with a component > 12 decides; a fully ambiguous file falls back by clock
style (AM/PM ⇒ US-shaped month-first, 24h ⇒ day-first). Exported times
carry no zone and are stored as UTC — consistent across re-imports, the same
policy gmail applies to unknown offsets.

Identity policy (WhatsApp exports carry no message ids):
``wa:<sha256(chat anchor + raw message block)>``, with a ``#N`` suffix for
the Nth identical block in one run. The fingerprint hashes the RAW exported
lines — never parsed/cleaned values — so parser evolution (timestamp fixes,
mark stripping, media parsing) can never re-mint identities and re-insert a
whole archive as duplicates (P2 finding 6). Identical blocks are real:
timestamps are minute-granular on Android, so two "ok" from one sender in
the same minute collide; first-seen ``#N`` order is stable for re-exports of
the same chat (WhatsApp appends). A truncated re-export that drops early
occurrences re-numbers only those duplicate groups.

Threading: every message in one chat shares chat_key — the chat file anchor
(basename for Android, parent directory for iOS ``_chat.txt``), so zip and
extracted-directory layouts agree. A root-level ``_chat.txt`` (the raw iOS
zip, whose chat name lives only in the zip's own filename, which plugins
never see) falls back to the generic ``_chat`` anchor. CONSEQUENCE: two
DIFFERENT chats imported as separate raw iOS zips both anchor to ``_chat``
— their threads merge, and because the identity fingerprint embeds the
anchor, byte-identical message blocks across the two conversations collide
on external_id and the second chat's copy silently dedups away (data loss,
not just cosmetics). Until the Archive seam exposes a source display name
(#210), extract such zips into their named folders before
importing. Messages are deliberately not parent_id-chained — chats are
linear.
"""

import hashlib
import logging
import mimetypes
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

from potluck.ingest.identity import occurrence_suffix
from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import MessageDraft, MessageMedia
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)

# Export naming across app generations: Android "WhatsApp Chat with X.txt"
# (any folder), iOS "_chat.txt" (root or nested). Each alternative is precise
# so the generic text ingester (#150) can never collide: "notes.txt" or
# "my_chat.txt" match nothing here.
_EXPORT_GLOB = Glob("*WhatsApp Chat*.txt|_chat.txt|*/_chat.txt")

# Invisible marks WhatsApp sprinkles at line/segment starts (LRM, RLM) plus
# the BOM; stripped for prefix matching and body-shape detection only — the
# raw lines keep them (identity input).
_MARKS = "\u200e\u200f\ufeff"  # LRM, RLM, BOM

# Timestamp prefix, shared by both line shapes. Date separators must repeat
# ("17/03.23" is not a date); the meridiem tolerates dots/case/space (a.m.,
# PM, "p. m."). Seconds are iOS-only in practice but accepted anywhere.
_TS_PART = (
    r"(?P<d1>\d{1,4})(?P<sep>[./-])(?P<d2>\d{1,2})(?P=sep)(?P<d3>\d{1,4}),? "
    r"(?P<hh>\d{1,2}):(?P<mm>\d{2})(?::(?P<ss>\d{2}))?"
    r"(?: ?(?P<ampm>[AaPp])\.? ?[Mm]\.?)?"
)
_BRACKET_RE = re.compile(rf"^\[{_TS_PART}\] (?P<rest>.*)$")
_DASH_RE = re.compile(rf"^{_TS_PART} - (?P<rest>.*)$")

# Media placeholder shapes (English exports; see module docstring).
_OMITTED_RE = re.compile(
    r"^(?:image|video|audio|sticker|gif|document|contact card) omitted$", re.IGNORECASE
)
_ATTACHED_RE = re.compile(r"^<attached: (?P<fn>[^<>]+)>$")
_FILE_ATTACHED_RE = re.compile(r"^(?P<fn>\S.*) \(file attached\)$")
_MEDIA_OMITTED = "<media omitted>"

# The one sender-attributed system shape: iOS pins the encryption notice to
# a participant. Never real user content in practice.
_SYSTEM_BODY_RE = re.compile(
    r"^Messages (?:and calls are end-to-end encrypted"
    r"|to this (?:chat|group) are now secured with end-to-end encryption)"
)


@dataclass(frozen=True, slots=True)
class _Prefix:
    """One parsed timestamp prefix; date components still order-ambiguous."""

    d1: int
    d2: int
    d3: int
    hh: int
    mm: int
    ss: int
    ampm: str | None
    rest: str


def _clean(line: str) -> str:
    """Prefix-matching copy: drop leading invisible marks, normalize the
    narrow/no-break spaces iOS puts before AM/PM to plain spaces."""
    return line.lstrip(_MARKS).replace("\u202f", " ").replace("\u00a0", " ")


def _match_prefix(line: str) -> _Prefix | None:
    """Match *line*'s timestamp prefix against its cleaned copy.

    rest is sliced from the ORIGINAL line: _clean's space substitutions are
    1:1 in length, so only the leading-mark strip shifts offsets — body
    NBSPs are content and must survive on the first line exactly as
    continuation lines keep theirs.
    """
    cleaned = _clean(line)
    m = _BRACKET_RE.match(cleaned) or _DASH_RE.match(cleaned)
    if m is None:
        return None
    offset = len(line) - len(cleaned)
    return _Prefix(
        d1=int(m["d1"]),
        d2=int(m["d2"]),
        d3=int(m["d3"]),
        hh=int(m["hh"]),
        mm=int(m["mm"]),
        ss=int(m["ss"] or 0),
        ampm=(m["ampm"] or "").upper() or None,
        rest=line[m.start("rest") + offset :],
    )


def _infer_day_first(lines: list[str]) -> bool:
    """One cheap pass over the timestamp prefixes; the first decisive date
    (a day/month component > 12) wins and exits early.

    A fully ambiguous file (every component <= 12) defaults by clock style:
    12-hour exports are US-shaped (month-first), 24-hour exports follow the
    rest of the world (day-first).
    """
    saw_ampm = False
    for line in lines:
        p = _match_prefix(line)
        if p is None or p.d1 >= 100:  # year-first dates are order-unambiguous
            continue
        if p.d1 > 12 >= p.d2:
            return True
        if p.d2 > 12 >= p.d1:
            return False
        saw_ampm = saw_ampm or p.ampm is not None
    return not saw_ampm


def _resolve_ts(p: _Prefix, day_first: bool) -> datetime:
    """Resolve a prefix to an aware datetime (exports carry no zone → UTC).

    Raises ValueError for impossible dates (Feb 31) — contained per message
    by _flush.
    """
    if p.d1 >= 100:
        year, month, day = p.d1, p.d2, p.d3
    else:
        year = p.d3 if p.d3 >= 100 else 2000 + p.d3
        day, month = (p.d1, p.d2) if day_first else (p.d2, p.d1)
    hour = p.hh
    if p.ampm == "P" and hour != 12:
        hour += 12
    elif p.ampm == "A" and hour == 12:
        hour = 0
    return datetime(year, month, day, hour, p.mm, p.ss, tzinfo=UTC)


def _chat_identity(member_name: str) -> tuple[str, str]:
    """(chat_key, chat_name) from the member path — see module docstring.

    The ``_chat`` fallback for a root-level ``_chat.txt`` is a shared anchor:
    different chats imported that way merge threads AND can lose
    byte-identical messages to cross-chat external_id collisions (the
    fingerprint embeds this anchor). Full consequence + workaround in the
    module docstring; fixing it needs the Archive seam to expose a source
    display name (#210).
    """
    parts = member_name.split("/")
    base = parts[-1]
    if base == "_chat.txt":
        anchor = parts[-2] if len(parts) > 1 else "_chat"
    else:
        anchor = base.removesuffix(".txt")
    name = anchor.removeprefix("WhatsApp Chat with ").removeprefix("WhatsApp Chat - ")
    return anchor, name


def _fingerprint(chat_key: str, raw_block: str) -> str:
    raw = f"{chat_key}\x1e{raw_block}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()


def _build_draft(
    prefix: _Prefix,
    raw_lines: list[str],
    body_lines: list[str],
    *,
    chat_key: str,
    chat_name: str,
    day_first: bool,
    counters: dict[str, int],
) -> MessageDraft | None:
    """Assemble one message block into a draft; None = system message.

    Raises ValueError for an unresolvable timestamp (contained by _flush).
    """
    sender, sep, first_body = prefix.rest.lstrip(_MARKS).partition(": ")
    if not sep or not sender:
        return None  # sender-less line: system chrome, never an item

    shape = first_body.lstrip(_MARKS).strip()
    if _SYSTEM_BODY_RE.match(shape):
        return None  # iOS sender-attributed encryption notice

    ts = _resolve_ts(prefix, day_first)

    is_media = False
    media: tuple[MessageMedia, ...] = ()
    text_head: str | None = first_body
    if shape.lower() == _MEDIA_OMITTED or _OMITTED_RE.match(shape):
        is_media, text_head = True, None
    elif (m := _ATTACHED_RE.match(shape)) or (m := _FILE_ATTACHED_RE.match(shape)):
        filename = m["fn"].strip()
        media = (MessageMedia(filename=filename, mime=mimetypes.guess_type(filename)[0]),)
        is_media, text_head = True, None

    pieces = ([] if text_head is None else [text_head]) + body_lines
    text = "\n".join(pieces).strip() or None

    fingerprint = _fingerprint(chat_key, "\n".join(raw_lines))
    suffix = occurrence_suffix(counters, fingerprint)

    return MessageDraft(
        external_id=f"wa:{fingerprint}{suffix}",
        ts=ts,
        text=text,
        chat_key=chat_key,
        chat_name=chat_name,
        sender=sender,
        is_media=is_media,
        media=media,
    )


@dataclass(slots=True)
class _Block:
    """One message block being accumulated: prefix + its physical lines."""

    prefix: _Prefix
    raw_lines: list[str]  # exactly as exported (identity input)
    body_lines: list[str]  # continuation lines, leading marks stripped


def _flush(
    block: _Block,
    *,
    member_name: str,
    chat_key: str,
    chat_name: str,
    day_first: bool,
    counters: dict[str, int],
) -> Iterator[MessageDraft]:
    """Convert one block, containing per-message errors (never abort a chat)."""
    try:
        draft = _build_draft(
            block.prefix,
            block.raw_lines,
            block.body_lines,
            chat_key=chat_key,
            chat_name=chat_name,
            day_first=day_first,
            counters=counters,
        )
    except ValueError as exc:
        _logger.warning("whatsapp: skipping unparseable message in %r: %s", member_name, exc)
        return
    if draft is not None:
        yield draft


def _parse_chat(data: bytes, member_name: str, counters: dict[str, int]) -> Iterator[MessageDraft]:
    """Yield drafts from one chat file. *counters* is the run-wide occurrence
    map for the ``#N`` identity suffixes (fingerprints embed the chat key, so
    sharing one map across chats is safe).

    A non-empty member where no line matches any timestamp dialect logs one
    WARNING (same containment policy as invalid dates): a detected export in
    an unsupported dialect must never import as zero items silently.
    """
    text = data.decode("utf-8", errors="replace")
    lines = [line.rstrip("\r") for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()  # trailing-newline artifact, not an empty continuation

    day_first = _infer_day_first(lines)
    chat_key, chat_name = _chat_identity(member_name)

    block: _Block | None = None
    matched_any = False
    for line in lines:
        prefix = _match_prefix(line)
        if prefix is None:
            if block is not None:  # pre-header junk has no block to join
                block.raw_lines.append(line)
                block.body_lines.append(line.lstrip(_MARKS))
            continue
        matched_any = True
        if block is not None:
            yield from _flush(
                block,
                member_name=member_name,
                chat_key=chat_key,
                chat_name=chat_name,
                day_first=day_first,
                counters=counters,
            )
        block = _Block(prefix=prefix, raw_lines=[line], body_lines=[])
    if block is not None:
        yield from _flush(
            block,
            member_name=member_name,
            chat_key=chat_key,
            chat_name=chat_name,
            day_first=day_first,
            counters=counters,
        )
    if not matched_any and any(line.strip() for line in lines):
        _logger.warning(
            "whatsapp: %r matched detection but no line has a recognizable "
            "timestamp header (unsupported dialect?) — 0 messages imported",
            member_name,
        )


@source(
    name="whatsapp",
    detect=_EXPORT_GLOB,
    kinds=(ItemKind.MESSAGE,),
    parser_version=1,
)
def parse(archive: Archive, ctx: ParseContext) -> Iterator[MessageDraft]:
    """Yield MessageDrafts from every chat-export member, one streaming pass.

    Only chat .txt files are read — media members are skipped unopened, and
    the single pattern pass keeps tar archives sequential. Per file the work
    is two passes over the decoded lines (locale inference exits at the first
    decisive date; the parse pass then streams blocks), so memory is bounded
    by one chat file plus the current message block. ctx is part of the
    plugin contract but unused: regex matching is a tiny fraction of an
    import, so there is nothing to parallelize.
    """
    counters: dict[str, int] = {}
    for member, stream in archive.iter_members("*.txt"):
        if not _EXPORT_GLOB.matches(member.name):
            continue
        yield from _parse_chat(stream.read(), member.name, counters)
