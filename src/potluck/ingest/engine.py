"""Batch ingest engine: drives import runs with content-hash dedup.

Per batch: pure hashing/dedup outside the writer thread, then one write
transaction that classifies against the DB (two IN(...) lookups), applies
one executemany per statement shape, and hands satellite-kind drafts to
their SatelliteWriter — all batch-first, no per-item round-trips.
"""

import contextlib
import itertools
import sqlite3
from collections.abc import Iterator
from concurrent.futures import Future
from dataclasses import dataclass, field
from functools import partial
from typing import Final

from potluck.ingest.hashing import content_hash
from potluck.models.drafts import ItemDraft
from potluck.models.items import ItemKind
from potluck.storage.db import Database
from potluck.storage.imports import begin_import, ensure_source, finish_import, record_batch
from potluck.storage.items import (
    ContentUpdate,
    ItemRow,
    MetaUpdate,
    draft_to_row,
    existing_by_external_id,
    existing_hashes,
    insert_items,
    update_items_content,
    update_items_meta,
)
from potluck.storage.lifecycle import suppressed_subset
from potluck.storage.satellites import SATELLITE_WRITERS

DEFAULT_BATCH_SIZE: Final = 1000


@dataclass
class _BatchPlan:
    """One batch classified into write shapes (built read-only, then applied)."""

    inserts: list[ItemRow] = field(default_factory=list)
    content_updates: list[ContentUpdate] = field(default_factory=list)
    meta_updates: list[MetaUpdate] = field(default_factory=list)
    # Hashes whose rows were displaced by a content UPDATE — they leave the DB,
    # so they must also leave the run-wide seen set (last-wins on revert).
    displaced_hashes: list[str] = field(default_factory=list)
    duplicates: int = 0
    # Drafts dropped because their hash is in suppressed_hashes (#153) —
    # forgotten content never re-ingests; counted separately, never skipped.
    suppressed: int = 0
    # Satellite payloads (only drafts whose kind has a SatelliteWriter):
    # inserted drafts keyed by content_hash for the post-insert id select-back;
    # updated drafts already know their item id.
    inserted_by_hash: dict[str, ItemDraft] = field(default_factory=dict)
    updated_pairs: list[tuple[ItemDraft, int]] = field(default_factory=list)


def _dedup_in_run(
    hashed: list[tuple[ItemDraft, str]], seen: set[str]
) -> tuple[list[tuple[ItemDraft, str]], int]:
    """Drop hashes already seen this run and apply in-batch identity last-wins
    (same external_id twice in one batch displaces the earlier draft) in ONE
    in-order pass. Returns the surviving (draft, hash) pairs and the dup count.

    When a draft displaces an earlier one, the shadowed draft's hash leaves
    the seen set immediately — it never reaches the DB, so a later draft
    reverting to that exact content must classify as a fresh write, not an
    in-run duplicate, regardless of where batch boundaries fall.
    """
    in_run_dups = 0
    slot_by_eid: dict[str, int] = {}
    slots: list[tuple[ItemDraft, str] | None] = []
    for draft, h in hashed:
        if h in seen:
            in_run_dups += 1
            continue
        eid = draft.external_id
        if eid is not None and eid in slot_by_eid:
            displaced = slots[slot_by_eid[eid]]
            if displaced is not None:
                seen.discard(displaced[1])
            slots[slot_by_eid[eid]] = None
            in_run_dups += 1
        seen.add(h)
        if eid is not None:
            slot_by_eid[eid] = len(slots)
        slots.append((draft, h))
    return [pair for pair in slots if pair is not None], in_run_dups


def _classify_batch(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    import_id: int,
    new_pairs: list[tuple[ItemDraft, str]],
    in_run_dups: int,
) -> _BatchPlan:
    """Classify a deduped batch against the DB into a _BatchPlan.

    Hash-first: a hash already in the DB is an exact row, so an UPDATE never
    writes a content_hash that exists elsewhere. Satellite fields live inside
    the hash, so meta-only refreshes can never carry satellite changes.
    """
    plan = _BatchPlan(duplicates=in_run_dups)

    new_hashes = [h for _, h in new_pairs]
    # ONE IN(...) query for the whole batch; skip when nothing new.
    already_in_db = existing_hashes(conn, source_id, new_hashes) if new_hashes else set()
    # ONE anti-join against suppressed_hashes (#153): forgotten content is
    # dropped outright — no insert, and no update of an existing identity row
    # (the row keeps its old content; the banned revision never lands).
    suppressed = suppressed_subset(conn, new_hashes) if new_hashes else set()

    # ONE identity lookup for the whole batch (source_id is constant per run).
    batch_eids = [d.external_id for d, _ in new_pairs if d.external_id is not None]
    existing = existing_by_external_id(conn, source_id, batch_eids) if batch_eids else {}

    for draft, h in new_pairs:
        if h in suppressed:
            plan.suppressed += 1
            continue
        row = draft_to_row(draft, source_id=source_id, import_id=import_id, content_hash=h)
        ex = existing.get(draft.external_id) if draft.external_id is not None else None
        has_satellite = draft.kind in SATELLITE_WRITERS
        if h in already_in_db:
            # Exact content match; meta is outside the hash, so a meta-only
            # difference still refreshes the row.
            if ex is not None and row.meta != ex.meta:
                plan.meta_updates.append(MetaUpdate(import_id=import_id, meta=row.meta, id=ex.id))
            else:
                plan.duplicates += 1
        elif ex is not None:
            plan.displaced_hashes.append(ex.content_hash)
            plan.content_updates.append(
                ContentUpdate(
                    import_id=import_id,
                    kind=row.kind,
                    ts=row.ts,
                    title=row.title,
                    text=row.text,
                    lat=row.lat,
                    lon=row.lon,
                    content_hash=row.content_hash,
                    meta=row.meta,
                    id=ex.id,
                )
            )
            if has_satellite:
                plan.updated_pairs.append((draft, ex.id))
        else:
            plan.inserts.append(row)
            if has_satellite:
                plan.inserted_by_hash[h] = draft

    return plan


def _write_satellites(conn: sqlite3.Connection, source_id: int, plan: _BatchPlan) -> None:
    """Dispatch satellite-kind drafts to their writers, batch-first.

    Inserted ids are recovered with ONE select-back over the batch's hashes
    (executemany cannot RETURNING; UNIQUE(source_id, content_hash) makes the
    mapping exact). Kinds without a writer never reach here.
    """
    pairs: list[tuple[ItemDraft, int]] = list(plan.updated_pairs)
    if plan.inserted_by_hash:
        hashes = list(plan.inserted_by_hash)
        placeholders = ",".join("?" * len(hashes))
        rows = conn.execute(
            f"SELECT content_hash, id FROM items "
            f"WHERE source_id = ? AND content_hash IN ({placeholders})",
            [source_id, *hashes],
        ).fetchall()
        pairs.extend((plan.inserted_by_hash[str(r[0])], int(r[1])) for r in rows)

    by_kind: dict[ItemKind, list[tuple[ItemDraft, int]]] = {}
    for draft, item_id in pairs:
        by_kind.setdefault(draft.kind, []).append((draft, item_id))
    for kind, group in sorted(by_kind.items()):
        SATELLITE_WRITERS[kind].write_batch(conn, group)


def _write_batch(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    import_id: int,
    new_pairs: list[tuple[ItemDraft, str]],
    in_run_dups: int,
) -> list[str]:
    """Classify and apply one batch in one transaction; returns displaced hashes."""
    plan = _classify_batch(
        conn,
        source_id=source_id,
        import_id=import_id,
        new_pairs=new_pairs,
        in_run_dups=in_run_dups,
    )
    conn.execute("BEGIN")
    try:
        # ONE executemany per statement shape for the whole batch.
        update_items_content(conn, plan.content_updates)
        update_items_meta(conn, plan.meta_updates)
        insert_items(conn, plan.inserts)
        _write_satellites(conn, source_id, plan)
        record_batch(
            conn,
            import_id,
            new=len(plan.inserts),
            duplicate=plan.duplicates,
            updated=len(plan.content_updates) + len(plan.meta_updates),
            skipped=0,
            suppressed=plan.suppressed,
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return plan.displaced_hashes


def _finalize_satellites(conn: sqlite3.Connection, *, source_id: int, kinds: set[ItemKind]) -> None:
    """End-of-run reconciliation (e.g. email parent linking), once per kind."""
    for kind in sorted(kinds):
        writer = SATELLITE_WRITERS.get(kind)
        if writer is not None and writer.finalize is not None:
            writer.finalize(conn, source_id)


def run_import(
    db: Database,
    *,
    source_name: str,
    parser_version: int,
    drafts: Iterator[ItemDraft],
    path: str,
    file_hash: str | None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    extract_attachments: bool = False,
    items_total: int | None = None,
) -> int:
    """Drive a full import; returns import_id.

    The ledger row is the source of truth: status is 'completed' on success,
    'failed' (with error text) on any exception, which is then re-raised.

    Progress (#132) rides the existing per-batch cadence: record_batch bumps
    the row's counters inside each batch's commit — one small UPDATE per
    batch, never per item — so a poller always sees the last committed batch.
    items_total is stored as the expected denominator when the caller can
    know it cheaply; None means unknown (the engine never pre-scans a stream
    just to count).

    Identity and counters: drafts with an external_id have one logical row per
    (source, external_id) — a draft matching an existing row updates it in
    place (counted items_updated) when the content hash or canonical meta
    differs, and counts items_duplicate when identical. Drafts without an
    external_id keep pure content-hash dedup. Counter quirk: when the same
    external_id repeats *within* one batch the earlier draft is displaced and
    counted items_duplicate (last wins); across batches the later draft lands
    as an UPDATE and counts items_updated.

    skipped stays 0: the engine does not yet drop drafts for validation reasons.

    Suppression (#153): drafts whose content hash is in suppressed_hashes
    (forgotten content) are dropped per batch via one anti-join and counted
    items_suppressed. A repeat of a suppressed hash later in the same run
    counts items_duplicate (it duplicates the suppressed draft), and an
    existing identity row keeps its old content when its new revision is
    suppressed.
    """

    def _setup(conn: sqlite3.Connection) -> tuple[int, int]:
        sid = ensure_source(conn, source_name)
        iid = begin_import(
            conn,
            source_id=sid,
            path=path,
            file_hash=file_hash,
            parser_version=parser_version,
            extract_attachments=extract_attachments,
            items_total=items_total,
        )
        return sid, iid

    source_id, import_id = db.write(_setup)

    seen: set[str] = set()
    touched_kinds: set[ItemKind] = set()
    # Pipelining (#199): at most ONE write in flight. Batch N+1 is pulled and
    # hashed (the expensive parse work lives in the drafts generator) while
    # batch N commits on the writer thread; the future is then resolved BEFORE
    # batch N+1's dedup, so displaced hashes leave `seen` in time (the revert
    # ordering test pins this). Errors surface one batch later, still inside
    # the ledger-failure handler below.
    pending: Future[list[str]] | None = None

    try:
        with db.bulk_import_mode():
            for chunk in itertools.batched(drafts, batch_size, strict=False):
                # Hashing is pure computation — happens outside db.write.
                hashed = [(draft, content_hash(draft)) for draft in chunk]
                if pending is not None:
                    seen.difference_update(pending.result())
                new_pairs, in_run_dups = _dedup_in_run(hashed, seen)
                touched_kinds.update(draft.kind for draft, _ in new_pairs)

                pending = db.write_async(
                    partial(
                        _write_batch,
                        source_id=source_id,
                        import_id=import_id,
                        new_pairs=new_pairs,
                        in_run_dups=in_run_dups,
                    )
                )
            if pending is not None:
                pending.result()

            db.write(partial(_finalize_satellites, source_id=source_id, kinds=touched_kinds))

    except BaseException as exc:
        # BaseException: Ctrl-C / SystemExit must not leave the ledger row
        # 'running' forever (str() of KeyboardInterrupt is empty — fall back
        # to the type name).
        error_text = str(exc) or type(exc).__name__
        # best-effort ledger update; never mask the original error
        with contextlib.suppress(Exception):
            db.write(lambda conn: finish_import(conn, import_id, status="failed", error=error_text))
        raise

    db.write(lambda conn: finish_import(conn, import_id, status="completed"))
    return import_id
