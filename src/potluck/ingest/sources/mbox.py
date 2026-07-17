"""Standalone mbox source plugin (#150): a bare ``foo.mbox`` (or a folder of
them — Thunderbird-style per-folder exports) imports as email items.

GENERIC TIER: this plugin only applies when no specific source matched the
archive (see detect_sources). A ``Mail/``-structured Takeout still goes to
gmail — its ``*Mail/*.mbox`` glob is the specific tier — while a standalone
mbox never reaches gmail today (verified: no ``Mail/`` segment, no match).
Importing a lone mbox pulled OUT of a Takeout directly is the escape hatch
either way; it lands here under the ``mbox:`` namespace.

Everything substantive is shared with gmail, extracted rather than copied:
the streaming splitter + MIME decoding (potluck.ingest.mbox, P2) and the
draft mapping / identity policy (potluck.ingest.maildraft — ``mid:`` with
content-aware ``#N`` suffixes, ``noid:`` raw-derived fingerprints,
References-root thread keys). This source passes ``namespace="mbox:"``, so
external_ids read ``mbox:mid:<Message-ID>`` / ``mbox:noid:<sha256>``.
Identity is per-source by schema anyway: the same mail inside a gmail
Takeout AND a standalone mbox is two items — documented, not fought
(cross-source dedup is a locked non-goal, 002_schema_v1.sql).

Message-ID bookkeeping is run-wide like gmail's: the same message appearing
in two mbox members (Inbox + Archive folder exports) collides on external_id
and dedups; only genuinely different content under one Message-ID gets a
``#N`` suffix. Satellite: the existing emails table (no new migration).
Attachments follow the gmail posture — metadata always, blobs extracted only
when ctx.attachments_dir is set; a blob-store I/O failure fails the run.

Deliberately sequential: no worker pool. Gmail's fan-out exists for the
multi-GB Takeout shape; a standalone mbox is typically one mailbox, and the
single-threaded loop stays until a bench scenario proves otherwise
(absolute rule 3). Plugins may ignore ctx.workers by contract.
"""

import logging
from collections.abc import Iterator

from potluck.ingest.attachments import AttachmentStore
from potluck.ingest.maildraft import raw_body_sha, to_email_draft
from potluck.ingest.mbox import iter_mbox_messages, parse_email
from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import EmailDraft
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)

_EXPORT_GLOB = Glob("*.mbox")


@source(
    name="mbox",
    detect=_EXPORT_GLOB,
    kinds=(ItemKind.EMAIL,),
    parser_version=1,
    generic=True,
)
def parse(archive: Archive, ctx: ParseContext) -> Iterator[EmailDraft]:
    """Yield EmailDrafts from every mbox member, streaming.

    One corrupt message logs a WARNING and is skipped — it must never abort
    the import (the gmail containment); an attachment-store I/O failure
    (ENOSPC, permissions) raises and fails the run instead of hiding lost
    mail behind a 'completed' ledger row.
    """
    seen_msgids: dict[str, dict[str, int]] = {}
    sink = AttachmentStore(ctx.attachments_dir).save if ctx.attachments_dir is not None else None

    for member, stream in archive.iter_members("*.mbox"):
        for raw in iter_mbox_messages(stream):
            try:
                parsed = parse_email(raw, payload_sink=sink)
                yield to_email_draft(parsed, raw_body_sha(raw), seen_msgids, namespace="mbox:")
            except OSError:
                raise  # blob-store failure: fail the run, never skip mail
            except Exception as exc:  # noqa: BLE001 — containment: skip, never abort
                _logger.warning("mbox: skipping unparseable message in %s: %s", member.name, exc)
