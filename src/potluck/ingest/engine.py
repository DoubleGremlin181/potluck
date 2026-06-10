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
from potluck.storage.items import draft_to_row, existing_hashes, insert_items

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

    on_progress: called after each batch with the cumulative count of source
    items consumed (new + duplicates, i.e. every item yielded so far).

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

                rows_to_insert = [
                    draft_to_row(
                        draft,
                        source_id=source_id,
                        import_id=import_id,
                        content_hash=h,
                    )
                    for draft, h in new_pairs
                    if h not in already_in_db
                ]
                db_dups = len(new_pairs) - len(rows_to_insert)
                total_dups = in_run_dups + db_dups

                conn.execute("BEGIN")
                try:
                    # ONE executemany for the whole batch.
                    insert_items(conn, rows_to_insert)
                    record_batch(
                        conn,
                        import_id,
                        new=len(rows_to_insert),
                        duplicate=total_dups,
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
