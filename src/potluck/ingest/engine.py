"""Batch ingest engine: drives import runs with content-hash dedup."""

import contextlib
import itertools
import sqlite3
from collections.abc import Callable, Iterator
from typing import Final

from potluck.ingest.hashing import content_hash
from potluck.models.drafts import ItemDraft
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

DEFAULT_BATCH_SIZE: Final = 1000


def run_import(
    db: Database,
    *,
    source_name: str,
    parser_version: int,
    drafts: Iterator[ItemDraft],
    path: str,
    file_hash: str | None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_progress: Callable[[int], None] | None = None,
) -> int:
    """Drive a full import; returns import_id.

    The ledger row is the source of truth: status is 'completed' on success,
    'failed' (with error text) on any exception, which is then re-raised.

    Identity and counters: drafts with an external_id have one logical row per
    (source, external_id) — a draft matching an existing row updates it in
    place (counted items_updated) when the content hash or canonical meta
    differs, and counts items_duplicate when identical. Drafts without an
    external_id keep pure content-hash dedup. Counter quirk: when the same
    external_id repeats *within* one batch the earlier draft is displaced and
    counted items_duplicate (last wins); across batches the later draft lands
    as an UPDATE and counts items_updated.

    on_progress: called after each batch with the cumulative count of source
    items consumed (new + duplicates + updated, i.e. every item yielded so far).

    skipped stays 0: the engine does not yet drop drafts for validation reasons.
    """

    def _setup(conn: sqlite3.Connection) -> tuple[int, int]:
        sid = ensure_source(conn, source_name)
        iid = begin_import(
            conn,
            source_id=sid,
            path=path,
            file_hash=file_hash,
            parser_version=parser_version,
        )
        return sid, iid

    source_id, import_id = db.write(_setup)

    seen: set[str] = set()
    total_processed = 0

    try:
        for chunk in itertools.batched(drafts, batch_size, strict=False):
            # Hashing is pure computation — happens outside db.write.
            hashed: list[tuple[ItemDraft, str]] = [(draft, content_hash(draft)) for draft in chunk]

            # In-run dedup: drop hashes already seen in an earlier batch.
            in_run_dups = 0
            new_pairs: list[tuple[ItemDraft, str]] = []
            for draft, h in hashed:
                if h in seen:
                    in_run_dups += 1
                else:
                    seen.add(h)
                    new_pairs.append((draft, h))

            # In-batch identity dedup: same external_id twice → last wins.
            slot_by_eid: dict[str, int] = {}
            slots: list[tuple[ItemDraft, str] | None] = []
            for pair in new_pairs:
                eid = pair[0].external_id
                if eid is not None:
                    if eid in slot_by_eid:
                        slots[slot_by_eid[eid]] = None
                        in_run_dups += 1
                    slot_by_eid[eid] = len(slots)
                slots.append(pair)
            new_pairs = [pair for pair in slots if pair is not None]

            # Capture batch-local state for the write closure.
            _new_pairs = new_pairs
            _in_run_dups = in_run_dups

            def _write_batch(
                conn: sqlite3.Connection,
                new_pairs: list[tuple[ItemDraft, str]] = _new_pairs,
                in_run_dups: int = _in_run_dups,
            ) -> None:
                new_hashes = [h for _, h in new_pairs]
                # ONE IN(...) query for the whole batch; skip when nothing new.
                already_in_db = existing_hashes(conn, new_hashes) if new_hashes else set()

                # ONE identity lookup for the whole batch (source_id is constant
                # per run, so no composite IN is needed).
                batch_eids = [d.external_id for d, _ in new_pairs if d.external_id is not None]
                existing = (
                    existing_by_external_id(conn, source_id, batch_eids) if batch_eids else {}
                )

                # Classify hash-first: a hash already in the DB is an exact row,
                # so an UPDATE never writes a content_hash that exists elsewhere.
                rows_to_insert: list[ItemRow] = []
                content_updates: list[ContentUpdate] = []
                meta_updates: list[MetaUpdate] = []
                db_dups = 0
                for draft, h in new_pairs:
                    row = draft_to_row(
                        draft,
                        source_id=source_id,
                        import_id=import_id,
                        content_hash=h,
                    )
                    ex = existing.get(draft.external_id) if draft.external_id is not None else None
                    if h in already_in_db:
                        # Exact content match; meta is outside the hash, so a
                        # meta-only difference still refreshes the row.
                        if ex is not None and row.meta != ex.meta:
                            meta_updates.append(
                                MetaUpdate(import_id=import_id, meta=row.meta, id=ex.id)
                            )
                        else:
                            db_dups += 1
                    elif ex is not None:
                        content_updates.append(
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
                    else:
                        rows_to_insert.append(row)

                total_dups = in_run_dups + db_dups

                conn.execute("BEGIN")
                try:
                    # ONE executemany per statement shape for the whole batch.
                    update_items_content(conn, content_updates)
                    update_items_meta(conn, meta_updates)
                    insert_items(conn, rows_to_insert)
                    record_batch(
                        conn,
                        import_id,
                        new=len(rows_to_insert),
                        duplicate=total_dups,
                        updated=len(content_updates) + len(meta_updates),
                        skipped=0,
                    )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise

            db.write(_write_batch)
            total_processed += len(chunk)
            if on_progress is not None:
                on_progress(total_processed)

    except Exception as exc:
        error_text = str(exc)
        # best-effort ledger update; never mask the original error
        with contextlib.suppress(Exception):
            db.write(lambda conn: finish_import(conn, import_id, status="failed", error=error_text))
        raise

    db.write(lambda conn: finish_import(conn, import_id, status="completed"))
    return import_id
