"""Streaming mbox splitting + MIME email parsing (constant memory).

stdlib mailbox.mbox needs a seekable file path, which Archive streams cannot
provide — so the splitter here is a plain readline loop. Memory is bounded by
the largest single message, never the mbox size.

Format notes (Gmail Takeout): messages are delimited by ``From `` envelope
lines; Gmail quotes body lines that start with ``From `` as ``>From ``. v1
does not un-quote ``>From`` (mboxrd unquoting) — the artifact is harmless in
indexed text.
"""

import email.message
import email.parser
import email.policy
import email.utils
import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import IO, Final

from potluck.ingest.htmltext import html_to_text
from potluck.ingest.textclean import clean_text

_PARSER: Final = email.parser.BytesParser(policy=email.policy.default)


def iter_mbox_messages(stream: IO[bytes]) -> Iterator[bytes]:
    """Yield raw RFC 822 message bytes, one per mbox entry.

    The ``From `` envelope line is excluded — parsers don't want it. Content
    before the first envelope line is ignored.
    """
    buf = bytearray()
    in_message = False
    for line in stream:
        if line.startswith(b"From "):
            if in_message and buf:
                yield bytes(buf)
            buf.clear()
            in_message = True
        elif in_message:
            buf += line
    if in_message and buf:
        yield bytes(buf)


def normalize_msgid(raw: str | None) -> str | None:
    """Normalize a Message-ID: strip whitespace and angle brackets; empty -> None."""
    if raw is None:
        return None
    cleaned = raw.strip().strip("<>").strip()
    return cleaned or None


@dataclass(frozen=True)
class AttachmentInfo:
    """Attachment metadata; payload bytes are hashed and dropped during parse."""

    filename: str | None
    mime: str
    size_bytes: int
    sha256: str


def _scrub_surrogates(s: str) -> str:
    """Replace lone UTF-16 surrogates (U+D800-U+DFFF) so parsed strings are
    always valid UTF-8.

    The stdlib email package decodes undecodable raw header bytes with
    ``errors="surrogateescape"`` (unknown-8bit), and exotic charsets can
    decode junk into lone surrogates outright (utf-7 turns ``+2AA-`` into
    U+D800 without error). Such strings cannot be UTF-8 encoded — one bad
    header byte would crash content hashing and the SQLite TEXT bind. The
    engine stays strict; the parse boundary guarantees clean strings.

    Fast path is a single C-level encode probe — no regex scan; the replace
    path runs only on defective input.
    """
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        return s.encode("utf-8", errors="replace").decode("utf-8")
    return s


@dataclass(frozen=True)
class ParsedEmail:
    """One decoded email — an ingest-layer value, not a storage draft.

    Invariant: no string field (including attachment filename/mime) carries
    lone surrogates — every string UTF-8-encodes cleanly (hashing, SQLite).
    """

    message_id: str | None
    in_reply_to: str | None
    references: tuple[str, ...]
    from_addr: str | None
    from_name: str | None
    to_addrs: tuple[str, ...]
    to_names: tuple[str, ...]  # positionally aligned with to_addrs; "" = no display name
    cc_addrs: tuple[str, ...]
    cc_names: tuple[str, ...]
    bcc_addrs: tuple[str, ...]
    subject: str | None
    date: datetime | None
    text: str
    labels: tuple[str, ...]
    attachments: tuple[AttachmentInfo, ...]


def _header(msg: email.message.Message, name: str) -> str | None:
    """Read one header defensively: real-world headers can make the policy
    machinery raise on access; a broken header reads as absent."""
    try:
        value = msg.get(name)
    except Exception:  # noqa: BLE001 — hostile input; any header error means "absent"
        return None
    return _scrub_surrogates(str(value)) if value is not None else None


def _msgid_list(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return ()
    ids = (normalize_msgid(token) for token in raw.split())
    return tuple(i for i in ids if i is not None)


def _address_pairs(msg: email.message.Message, name: str) -> tuple[tuple[str, str], ...]:
    """(display_name, lowercased_addr) per mailbox; name is "" when absent.

    policy.default already parsed the header into structured Address objects
    — read them directly instead of re-serializing and re-running the RFC
    5322 parse through getaddresses (~77% of import time is MIME decoding).
    """
    try:
        # Address tokens carry surrogateescape'd junk bytes through (unlike
        # unstructured headers, which utils._sanitize already replaces) —
        # THE real-data crash path: one bad From display name killed a
        # 126k-email import in content_hash.
        return tuple(
            (_scrub_surrogates(a.display_name), _scrub_surrogates(a.addr_spec.lower()))
            for v in msg.get_all(name, [])
            for a in getattr(v, "addresses", ())
            if a.addr_spec
        )
    except Exception:  # noqa: BLE001 — see _header (defective headers raise on access)
        return ()


def _date(msg: email.message.Message) -> datetime | None:
    raw = _header(msg, "Date")
    if raw is None:
        return None
    try:
        return email.utils.parsedate_to_datetime(raw)
    except (ValueError, TypeError):
        return None


def _labels(msg: email.message.Message) -> tuple[str, ...]:
    raw = _header(msg, "X-Gmail-Labels")
    if raw is None:
        return ()
    return tuple(label for label in (part.strip() for part in raw.split(",")) if label)


def _decode_bytes(data: bytes, declared: str | None) -> str:
    """Charset fallback chain: declared -> utf-8 -> latin-1 with replacement."""
    for encoding in dict.fromkeys(filter(None, (declared, "utf-8"))):
        try:
            # Scrubbed because bytes.decode CAN emit lone surrogates for some
            # declared charsets (utf-7 decodes b"+2AA-" to U+D800 without
            # error) — body text must uphold the ParsedEmail invariant too.
            return _scrub_surrogates(data.decode(encoding))
        # ValueError covers UnicodeDecodeError AND the plain ValueError that
        # str.decode raises for charset names with embedded NULs (seen in
        # malformed mail) — junk charsets must fall through, never abort.
        except (LookupError, ValueError):
            continue
    # latin-1 maps bytes to U+0000-U+00FF only — no surrogates possible.
    return data.decode("latin-1", errors="replace")


def _payload_bytes(part: email.message.Message) -> bytes:
    """Decoded payload (handles base64/quoted-printable); broken CTE falls
    back to the raw payload string re-encoded as latin-1."""
    try:
        payload = part.get_payload(decode=True)
    except Exception:  # noqa: BLE001 — see _header
        payload = None
    if isinstance(payload, bytes):
        return payload
    raw = part.get_payload()
    if isinstance(raw, str):
        # errors="replace" also swallows surrogateescape'd junk in the raw
        # string ('\udc93' encodes as b"?") — this re-encode cannot raise.
        return raw.encode("latin-1", errors="replace")
    return b""


def _message_bytes(part: email.message.Message) -> bytes:
    """Serialized bytes of an attached message/* part (the whole inner email)."""
    sub = part.get_payload()
    if isinstance(sub, list) and sub and isinstance(sub[0], email.message.Message):
        try:
            return sub[0].as_bytes()
        except Exception:  # noqa: BLE001 — defective nested message; fall through
            pass
    return _payload_bytes(part)


# Defensive bound on MIME nesting; real mail is a handful of levels deep.
_MAX_MIME_DEPTH: Final = 100


def parse_email(
    raw: bytes, *, payload_sink: Callable[[str, bytes], object] | None = None
) -> ParsedEmail:
    """Decode one raw message: headers, body text, attachment metadata.

    Body selection: first non-attachment text/plain part wins; else the first
    text/html part is reduced via html_to_text. EVERY other leaf part — an
    attachment disposition, a non-text type, an extra inline text part (e.g.
    text/calendar), or an attached message/rfc822 (recorded whole, never
    descended into) — is recorded as an attachment: metadata only; payload
    bytes are hashed, offered to *payload_sink* (``(sha256, payload)`` — the
    extraction hook, #124), then discarded.
    """
    msg = _PARSER.parsebytes(raw)

    plain_part: email.message.Message | None = None
    html_part: email.message.Message | None = None
    attachments: list[AttachmentInfo] = []

    def _record_attachment(part: email.message.Message, payload: bytes) -> None:
        sha256 = hashlib.sha256(payload).hexdigest()
        if payload_sink is not None:
            payload_sink(sha256, payload)
        # filename/mime are header-derived (Content-Disposition/Content-Type)
        # and feed the content hash — scrubbed like every header string.
        filename = part.get_filename()
        attachments.append(
            AttachmentInfo(
                filename=_scrub_surrogates(filename) if filename is not None else None,
                mime=_scrub_surrogates(part.get_content_type()),
                size_bytes=len(payload),
                sha256=sha256,
            )
        )

    def _walk(part: email.message.Message, depth: int) -> None:
        nonlocal plain_part, html_part
        if depth > _MAX_MIME_DEPTH:
            return
        if depth > 0 and part.get_content_maintype() == "message":
            # An attached email is ONE attachment (the whole subtree); its
            # inner body must never be mis-attributed to the outer message.
            _record_attachment(part, _message_bytes(part))
            return
        if part.is_multipart():
            payload = part.get_payload()
            if isinstance(payload, list):
                for sub in payload:
                    if isinstance(sub, email.message.Message):
                        _walk(sub, depth + 1)
            return
        content_type = part.get_content_type()
        is_attachment = part.get_content_disposition() == "attachment"
        if not is_attachment and content_type == "text/plain" and plain_part is None:
            plain_part = part
        elif not is_attachment and content_type == "text/html" and html_part is None:
            html_part = part
        else:
            _record_attachment(part, _payload_bytes(part))

    _walk(msg, 0)

    # Body parts are decoded AFTER the walk: in the common multipart/alternative
    # case the plain part wins and the (usually larger) html part is never
    # CTE+charset-decoded at all.
    plain = (
        _decode_bytes(_payload_bytes(plain_part), plain_part.get_content_charset())
        if plain_part is not None
        else None
    )
    # Real Gmail exports sometimes carry an EMPTY text/plain alternative next
    # to a populated text/html — whitespace-only plain falls through to html.
    if plain is not None and plain.strip():
        text = plain
    elif html_part is not None:
        html = _decode_bytes(_payload_bytes(html_part), html_part.get_content_charset())
        text = html_to_text(html)
    else:
        text = plain or ""
    # Cleanup before ParsedEmail construction: fingerprints, content hashes,
    # and the stored/indexed text all see the same cleaned body (#199).
    text = clean_text(text)

    in_reply_to = _msgid_list(_header(msg, "In-Reply-To"))
    sender = next(iter(_address_pairs(msg, "From")), None)
    to_pairs = _address_pairs(msg, "To")
    cc_pairs = _address_pairs(msg, "Cc")

    return ParsedEmail(
        message_id=normalize_msgid(_header(msg, "Message-ID")),
        in_reply_to=in_reply_to[0] if in_reply_to else None,
        references=_msgid_list(_header(msg, "References")),
        from_addr=sender[1] if sender else None,
        from_name=(sender[0] or None) if sender else None,
        to_addrs=tuple(addr for _, addr in to_pairs),
        to_names=tuple(display for display, _ in to_pairs),
        cc_addrs=tuple(addr for _, addr in cc_pairs),
        cc_names=tuple(display for display, _ in cc_pairs),
        bcc_addrs=tuple(addr for _, addr in _address_pairs(msg, "Bcc")),
        subject=_header(msg, "Subject"),
        date=_date(msg),
        text=text,
        labels=_labels(msg),
        attachments=tuple(attachments),
    )
