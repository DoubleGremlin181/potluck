"""Gmail Takeout source plugin: streams the Mail mbox into EmailDrafts.

Identity policy:
- ``mid:<Message-ID>`` when the header is present. Repeats of one Message-ID
  with IDENTICAL raw body bytes (label-selected Takeout duplicates a
  multi-label message across mbox members) reuse the same external_id so
  content-hash dedup collapses them; only genuinely different content gets a
  ``#2``/``#3`` suffix (suffix order follows first-seen mbox order — stable
  for Gmail's append-ordered exports).
- ``noid:<sha256 of from/to/date/subject + raw-body sha>`` when absent. The
  fingerprint hashes raw-derived inputs only — never cleaned body text — so
  parser/textclean evolution can never re-mint identities and re-insert the
  whole no-Message-ID class as duplicates.

thread_key (deterministic, per-message): References root, else In-Reply-To,
else Message-ID, else the noid fingerprint. A deep reply carrying only
In-Reply-To threads under its parent rather than the root — acceptable;
Gmail exports carry full References.
"""

import hashlib
import itertools
import logging
import multiprocessing
import os
from collections import deque
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from multiprocessing.pool import AsyncResult
from pathlib import Path
from typing import Final

from potluck.ingest.attachments import AttachmentStore
from potluck.ingest.mbox import ParsedEmail, iter_mbox_messages, parse_email
from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import EmailAttachment, EmailDraft
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)

# Worker chunk: large enough to amortize IPC per message, small enough to
# keep all workers busy on a multi-mbox archive (#199).
_POOL_CHUNKSIZE = 64

# Cap on chunk results outstanding in the parent (pool.imap buffers results
# unboundedly when the consumer is slower than the workers): RSS stays at
# roughly (window + 1) x _POOL_CHUNKSIZE parsed messages.
_MAX_INFLIGHT_CHUNKS: Final = 8


def _raw_body_sha(raw: bytes) -> str:
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


def _fingerprint(parsed: ParsedEmail, body_sha: str) -> str:
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


def _aware_ts(date: datetime | None) -> datetime | None:
    """RFC 5322 ``-0000`` dates parse as naive — treat unknown offsets as UTC."""
    if date is None or date.tzinfo is not None:
        return date
    return date.replace(tzinfo=UTC)


def _to_draft(
    parsed: ParsedEmail, body_sha: str, seen_msgids: dict[str, dict[str, int]]
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
        external_id = f"mid:{parsed.message_id}{suffix}"
    else:
        external_id = _fingerprint(parsed, body_sha)

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


def _effective_workers(requested: int) -> int:
    """0 = auto: min(4, cpus) — measured flat beyond 4 (serial part dominates)."""
    if requested > 0:
        return requested
    return min(4, os.cpu_count() or 1)


# Per-worker attachment store, set once by the pool initializer. Workers write
# blobs themselves: payload bytes must never cross the process boundary.
_WORKER_STORE: AttachmentStore | None = None


def _pool_init(attachments_dir: Path | None) -> None:
    global _WORKER_STORE  # noqa: PLW0603 — process-local, set once at fork-less spawn
    _WORKER_STORE = AttachmentStore(attachments_dir) if attachments_dir is not None else None


@dataclass(frozen=True)
class _ParseFailure:
    """Containment sentinel: a worker exception travels back as data so one
    corrupt message can never poison the pool or abort the import."""

    error: str


@dataclass(frozen=True)
class _ParsedMessage:
    """A decoded message plus its raw-body sha (the stable identity input)."""

    parsed: ParsedEmail
    body_sha: str


def _parse_chunk(raws: Sequence[bytes]) -> list[_ParsedMessage | _ParseFailure]:
    """Top-level (spawn-safe) worker: one fixed chunk of raw messages.

    OSError — the attachment store hitting ENOSPC/EACCES — propagates and
    fails the run (a 'completed' ledger row must never hide lost mail);
    parse errors travel back as _ParseFailure data.
    """
    sink = _WORKER_STORE.save if _WORKER_STORE is not None else None
    out: list[_ParsedMessage | _ParseFailure] = []
    for raw in raws:
        try:
            out.append(_ParsedMessage(parse_email(raw, payload_sink=sink), _raw_body_sha(raw)))
        except OSError:
            raise  # re-raised in the parent by AsyncResult.get()
        except Exception as exc:  # noqa: BLE001 — see _ParseFailure
            out.append(_ParseFailure(f"{type(exc).__name__}: {exc}"))
    return out


def _drain(
    results: list[_ParsedMessage | _ParseFailure],
    member_name: str,
    seen_msgids: dict[str, dict[str, int]],
) -> Iterator[EmailDraft]:
    """Convert one chunk's results to drafts, containing per-message errors."""
    for result in results:
        if isinstance(result, _ParseFailure):
            _logger.warning("skipping unparseable message in %s: %s", member_name, result.error)
            continue
        try:
            yield _to_draft(result.parsed, result.body_sha, seen_msgids)
        except Exception as exc:  # noqa: BLE001 — containment: skip, never abort
            _logger.warning("skipping unparseable message in %s: %s", member_name, exc)


@source(
    name="gmail",
    detect=Glob("*Mail/*.mbox"),
    kinds=(ItemKind.EMAIL,),
    # v3 (#198 review): rfc822/inline-text parts become attachments, stable
    # noid fingerprints (raw-body sha), content-aware #N suffixes, attachment
    # filename+mime in the hash, head-skip HTML fix — content hashes and some
    # external_ids changed, so existing archives re-ingest (updates in place).
    parser_version=3,
)
def parse(archive: Archive, ctx: ParseContext) -> Iterator[EmailDraft]:
    """Yield EmailDrafts from every Mail mbox member, streaming.

    MIME decoding (77% of a measured real import) fans out to ctx.workers
    processes in fixed chunks (#199), with at most _MAX_INFLIGHT_CHUNKS chunk
    results outstanding — input read-ahead AND buffered results stay bounded
    even when the serial parent is the bottleneck. Chunks are drained FIFO,
    and ``_to_draft`` stays sequential in the parent, so msgid ``#N`` suffixes
    — and therefore the resulting database — are identical to a sequential
    run. workers == 1 skips the pool entirely.

    One corrupt message logs a WARNING and is skipped — it must never abort
    a multi-GB import. Attachment blobs are extracted content-addressed only
    when ctx.attachments_dir is set (metadata is recorded either way); a
    blob-store I/O failure (ENOSPC, permissions) raises and fails the run.
    """
    workers = _effective_workers(ctx.workers)
    seen_msgids: dict[str, dict[str, int]] = {}

    if workers == 1:
        sink = (
            AttachmentStore(ctx.attachments_dir).save if ctx.attachments_dir is not None else None
        )
        for member, stream in archive.iter_members("*Mail/*.mbox"):
            for raw in iter_mbox_messages(stream):
                try:
                    parsed = parse_email(raw, payload_sink=sink)
                    yield _to_draft(parsed, _raw_body_sha(raw), seen_msgids)
                except OSError:
                    raise  # blob-store failure: fail the run, never skip mail
                except Exception as exc:  # noqa: BLE001 — containment: skip, never abort
                    _logger.warning("skipping unparseable message in %s: %s", member.name, exc)
        return

    # spawn, never fork: the parent holds live threads (the DB writer); fork
    # would clone their locks mid-flight.
    mp = multiprocessing.get_context("spawn")
    with mp.Pool(workers, initializer=_pool_init, initargs=(ctx.attachments_dir,)) as pool:
        for member, stream in archive.iter_members("*Mail/*.mbox"):
            inflight: deque[AsyncResult[list[_ParsedMessage | _ParseFailure]]] = deque()
            chunks = itertools.batched(iter_mbox_messages(stream), _POOL_CHUNKSIZE, strict=False)
            for chunk in chunks:
                if len(inflight) >= _MAX_INFLIGHT_CHUNKS:
                    yield from _drain(inflight.popleft().get(), member.name, seen_msgids)
                inflight.append(pool.apply_async(_parse_chunk, (chunk,)))
            while inflight:
                yield from _drain(inflight.popleft().get(), member.name, seen_msgids)
