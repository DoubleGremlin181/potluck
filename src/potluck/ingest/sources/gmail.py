"""Gmail Takeout source plugin: streams the Mail mbox into EmailDrafts.

Identity policy and the ParsedEmail → EmailDraft mapping live in
potluck.ingest.maildraft (extracted for the standalone mbox source, #150 —
gmail's recipe is the namespace-less default and is byte-for-byte unchanged):
``mid:<Message-ID>`` with content-aware ``#N`` suffixes, ``noid:<sha256>``
fingerprints over raw-derived inputs, References-root thread keys.
"""

import itertools
import logging
import multiprocessing
import os
from collections import deque
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from multiprocessing.pool import AsyncResult
from pathlib import Path
from typing import Final

from potluck.ingest.attachments import AttachmentStore
from potluck.ingest.maildraft import raw_body_sha, to_email_draft
from potluck.ingest.mbox import ParsedEmail, iter_mbox_messages, parse_email
from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import EmailDraft
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)

# Worker chunk: large enough to amortize IPC per message, small enough to
# keep all workers busy on a multi-mbox archive (#199).
_POOL_CHUNKSIZE = 64

# Cap on chunk results outstanding in the parent (pool.imap buffers results
# unboundedly when the consumer is slower than the workers): RSS stays at
# roughly (window + 1) x _POOL_CHUNKSIZE parsed messages.
_MAX_INFLIGHT_CHUNKS: Final = 8


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
            out.append(_ParsedMessage(parse_email(raw, payload_sink=sink), raw_body_sha(raw)))
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
            yield to_email_draft(result.parsed, result.body_sha, seen_msgids)
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
    and ``to_email_draft`` stays sequential in the parent, so msgid ``#N`` suffixes
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
                    yield to_email_draft(parsed, raw_body_sha(raw), seen_msgids)
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
