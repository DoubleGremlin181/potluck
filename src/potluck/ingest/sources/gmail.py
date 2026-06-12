"""Gmail Takeout source plugin: streams the Mail mbox into EmailDrafts.

Identity policy:
- ``mid:<Message-ID>`` when the header is present; an in-mbox duplicate gets
  a ``#2``/``#3`` suffix so two different messages sharing a Message-ID never
  collapse into one logical item (suffix order follows mbox order — stable
  for Gmail's append-ordered exports).
- ``noid:<sha256 of from/to/date/subject/first-1KB-of-body>`` when absent.

thread_key (deterministic, per-message): References root, else In-Reply-To,
else Message-ID, else the noid fingerprint. A deep reply carrying only
In-Reply-To threads under its parent rather than the root — acceptable;
Gmail exports carry full References.
"""

import hashlib
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from potluck.ingest.attachments import AttachmentStore
from potluck.ingest.mbox import ParsedEmail, iter_mbox_messages, parse_email
from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import EmailAttachment, EmailDraft
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)

_FINGERPRINT_BODY_CHARS = 1024


def _fingerprint(parsed: ParsedEmail) -> str:
    """Content identity for messages without a Message-ID."""
    parts = (
        parsed.from_addr or "",
        "\x1f".join(parsed.to_addrs),
        parsed.date.isoformat() if parsed.date is not None else "",
        parsed.subject or "",
        parsed.text[:_FINGERPRINT_BODY_CHARS],
    )
    raw = "\x1e".join(parts).encode("utf-8", errors="replace")
    return "noid:" + hashlib.sha256(raw).hexdigest()


def _aware_ts(date: datetime | None) -> datetime | None:
    """RFC 5322 ``-0000`` dates parse as naive — treat unknown offsets as UTC."""
    if date is None or date.tzinfo is not None:
        return date
    return date.replace(tzinfo=UTC)


def _to_draft(parsed: ParsedEmail, seen_msgids: dict[str, int]) -> EmailDraft:
    """Map one decoded message to a draft, applying the identity policy.

    *seen_msgids* tracks Message-ID occurrences across the whole run (a few
    MB at 50k messages) to suffix in-mbox duplicates.
    """
    if parsed.message_id is not None:
        count = seen_msgids.get(parsed.message_id, 0) + 1
        seen_msgids[parsed.message_id] = count
        suffix = "" if count == 1 else f"#{count}"
        external_id = f"mid:{parsed.message_id}{suffix}"
    else:
        external_id = _fingerprint(parsed)

    thread_key = (
        (parsed.references[0] if parsed.references else None)
        or parsed.in_reply_to
        or parsed.message_id
        or external_id
    )

    return EmailDraft(
        external_id=external_id,
        ts=_aware_ts(parsed.date),
        title=parsed.subject,
        text=parsed.text or None,
        message_id=parsed.message_id,
        in_reply_to=parsed.in_reply_to,
        thread_key=thread_key,
        from_addr=parsed.from_addr,
        from_name=parsed.from_name,
        to_addrs=parsed.to_addrs,
        to_names=parsed.to_names,
        cc_addrs=parsed.cc_addrs,
        cc_names=parsed.cc_names,
        bcc_addrs=parsed.bcc_addrs,
        labels=parsed.labels,
        attachments=tuple(
            EmailAttachment(
                filename=att.filename,
                mime=att.mime,
                size_bytes=att.size_bytes,
                sha256=att.sha256,
            )
            for att in parsed.attachments
        ),
    )


@source(
    name="gmail",
    detect=Glob("*Mail/*.mbox"),
    kinds=(ItemKind.EMAIL,),
    # v2 (#199): body text cleanup + from_name/to_names/cc_names/bcc fields —
    # content hashes changed, so existing archives re-ingest (updates in place).
    parser_version=2,
)
def parse(archive: Archive, ctx: ParseContext) -> Iterator[EmailDraft]:
    """Yield EmailDrafts from every Mail mbox member, streaming.

    One corrupt message logs a WARNING and is skipped — it must never abort
    a multi-GB import. Attachment blobs are extracted content-addressed only
    when ctx.attachments_dir is set (metadata is recorded either way).
    """
    sink = AttachmentStore(ctx.attachments_dir).save if ctx.attachments_dir is not None else None
    seen_msgids: dict[str, int] = {}
    for member, stream in archive.iter_members("*Mail/*.mbox"):
        for raw in iter_mbox_messages(stream):
            try:
                yield _to_draft(parse_email(raw, payload_sink=sink), seen_msgids)
            except Exception as exc:  # noqa: BLE001 — containment: skip, never abort
                _logger.warning("skipping unparseable message in %s: %s", member.name, exc)
