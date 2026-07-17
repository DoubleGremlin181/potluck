"""Shared ParsedEmail → EmailDraft mapping: THE mail identity policy.

Extracted from the gmail source (#125/#198) for reuse by the standalone mbox
source (#150) — gmail's behavior is unchanged (namespace defaults to "").

Identity policy:
- ``mid:<Message-ID>`` when the header is present. Repeats of one Message-ID
  with IDENTICAL raw body bytes (label-selected Takeout duplicates a
  multi-label message across mbox members) reuse the same external_id so
  content-hash dedup collapses them; only genuinely different content gets a
  ``#2``/``#3`` suffix (suffix order follows first-seen mbox order — stable
  for append-ordered exports).
- ``noid:<sha256 of from/to/date/subject + raw-body sha>`` when absent. The
  fingerprint hashes raw-derived inputs only — never cleaned body text — so
  parser/textclean evolution can never re-mint identities and re-insert the
  whole no-Message-ID class as duplicates.
- *namespace* prefixes the external_id verbatim (the mbox source passes
  ``"mbox:"``). Identity is per-source by schema anyway; the prefix makes
  rows self-describing and keeps the two sources' recipes visibly distinct.

thread_key (deterministic, per-message): References root, else In-Reply-To,
else Message-ID, else the (namespaced) noid fingerprint. A deep reply
carrying only In-Reply-To threads under its parent rather than the root —
acceptable; Gmail exports carry full References.
"""

import hashlib
from datetime import UTC, datetime

from potluck.ingest.mbox import ParsedEmail
from potluck.models.drafts import EmailAttachment, EmailDraft


def raw_body_sha(raw: bytes) -> str:
    """sha256 of the raw body bytes (everything after the header block).

    THE stable identity input: unaffected by parser/textclean evolution AND
    by header-only differences (e.g. X-Gmail-Labels varying across exports),
    so it can anchor both noid fingerprints and duplicate-Message-ID
    bookkeeping across re-imports.
    """
    found = [(idx, len(sep)) for sep in (b"\r\n\r\n", b"\n\n") if (idx := raw.find(sep)) != -1]
    if not found:
        return hashlib.sha256(b"").hexdigest()
    idx, sep_len = min(found)
    return hashlib.sha256(raw[idx + sep_len :]).hexdigest()


def noid_fingerprint(parsed: ParsedEmail, body_sha: str) -> str:
    """Content identity for messages without a Message-ID.

    Header fields keep label-only re-exports stable (labels are NOT hashed
    here, so a label change updates the item in place); the raw-body sha —
    never the cleaned text — makes the fingerprint immune to text-cleanup
    changes (#198 review).
    """
    parts = (
        parsed.from_addr or "",
        "\x1f".join(parsed.to_addrs),
        parsed.date.isoformat() if parsed.date is not None else "",
        parsed.subject or "",
        body_sha,
    )
    raw = "\x1e".join(parts).encode("utf-8", errors="replace")
    return "noid:" + hashlib.sha256(raw).hexdigest()


def aware_ts(date: datetime | None) -> datetime | None:
    """RFC 5322 ``-0000`` dates parse as naive — treat unknown offsets as UTC."""
    if date is None or date.tzinfo is not None:
        return date
    return date.replace(tzinfo=UTC)


def to_email_draft(
    parsed: ParsedEmail,
    body_sha: str,
    seen_msgids: dict[str, dict[str, int]],
    *,
    namespace: str = "",
) -> EmailDraft:
    """Map one decoded message to a draft, applying the identity policy.

    *seen_msgids* maps Message-ID -> {body sha -> suffix number} across the
    whole run (a few MB at 50k messages): byte-identical repeats reuse their
    external_id and dedup away; only differing content gets a ``#N`` suffix.
    """
    if parsed.message_id is not None:
        per_msg = seen_msgids.setdefault(parsed.message_id, {})
        number = per_msg.setdefault(body_sha, len(per_msg) + 1)
        suffix = "" if number == 1 else f"#{number}"
        external_id = f"{namespace}mid:{parsed.message_id}{suffix}"
    else:
        external_id = namespace + noid_fingerprint(parsed, body_sha)

    thread_key = (
        (parsed.references[0] if parsed.references else None)
        or parsed.in_reply_to
        or parsed.message_id
        or external_id
    )

    return EmailDraft(
        external_id=external_id,
        ts=aware_ts(parsed.date),
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
