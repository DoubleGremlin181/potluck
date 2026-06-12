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


@dataclass(frozen=True)
class ParsedEmail:
    """One decoded email — an ingest-layer value, not a storage draft."""

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
    return str(value) if value is not None else None


def _msgid_list(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return ()
    ids = (normalize_msgid(token) for token in raw.split())
    return tuple(i for i in ids if i is not None)


def _address_pairs(msg: email.message.Message, name: str) -> tuple[tuple[str, str], ...]:
    """(display_name, lowercased_addr) per mailbox; name is "" when absent."""
    try:
        values = [str(v) for v in msg.get_all(name, [])]
    except Exception:  # noqa: BLE001 — see _header
        return ()
    pairs = email.utils.getaddresses(values)
    return tuple((display, addr.lower()) for display, addr in pairs if addr)


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
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
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
        return raw.encode("latin-1", errors="replace")
    return b""


def parse_email(
    raw: bytes, *, payload_sink: Callable[[str, bytes], object] | None = None
) -> ParsedEmail:
    """Decode one raw message: headers, body text, attachment metadata.

    Body selection: first non-attachment text/plain part wins; else the first
    text/html part is reduced via html_to_text. Every non-text leaf part (or
    any part with an attachment disposition) is recorded as an attachment —
    metadata only; payload bytes are hashed, offered to *payload_sink*
    (``(sha256, payload)`` — the extraction hook, #124), then discarded.
    """
    msg = _PARSER.parsebytes(raw)

    plain: str | None = None
    html: str | None = None
    attachments: list[AttachmentInfo] = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        is_attachment = part.get_content_disposition() == "attachment"
        if not is_attachment and content_type == "text/plain" and plain is None:
            plain = _decode_bytes(_payload_bytes(part), part.get_content_charset())
        elif not is_attachment and content_type == "text/html" and html is None:
            html = _decode_bytes(_payload_bytes(part), part.get_content_charset())
        elif is_attachment or not content_type.startswith("text/"):
            payload = _payload_bytes(part)
            sha256 = hashlib.sha256(payload).hexdigest()
            if payload_sink is not None:
                payload_sink(sha256, payload)
            attachments.append(
                AttachmentInfo(
                    filename=part.get_filename(),
                    mime=content_type,
                    size_bytes=len(payload),
                    sha256=sha256,
                )
            )

    # Real Gmail exports sometimes carry an EMPTY text/plain alternative next
    # to a populated text/html — whitespace-only plain falls through to html.
    if plain is not None and plain.strip():
        text = plain
    elif html is not None:
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
