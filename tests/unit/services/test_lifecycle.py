"""Tests for services.lifecycle: rm/forget cascading deletes + suppressed_hashes (#153).

Covers the three selectors (import / source / item ids), the forget variant,
the engine's batch anti-join against suppressed_hashes, orphan-impossibility
(satellites, files, FTS, foreign keys), and single-transaction observability.
"""

import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from potluck.core.errors import (
    ImportNotFoundError,
    ImportRunningError,
    ItemNotFoundError,
    SourceNotFoundError,
)
from potluck.ingest.engine import run_import
from potluck.ingest.hashing import content_hash
from potluck.models.drafts import (
    EmailAttachment,
    ItemDraft,
    LocationDraft,
    MessageDraft,
    NoteDraft,
    PhotoDraft,
    TransactionDraft,
)
from potluck.services import lifecycle as lifecycle_service
from potluck.services.context import AppContext
from potluck.services.imports import import_path
from potluck.storage.fts import integrity_check
from potluck.testing.keep import write_keep_takeout
from tests.conftest import email_draft, insert_import, insert_item, insert_source

# Every table with an item_id FK — the orphan check runs generically over all.
_CHILD_TABLES = ("emails", "messages", "transactions", "locations", "media", "files")

_TS = datetime(2024, 3, 1, tzinfo=UTC)


def _mixed_drafts() -> list[ItemDraft]:
    """Seven drafts covering every satellite table plus files (attachment)."""
    return [
        email_draft(
            1,
            attachments=(
                EmailAttachment(
                    filename="a.pdf", mime="application/pdf", size_bytes=10, sha256="ab" * 32
                ),
            ),
        ),
        email_draft(2, in_reply_to="m1@potluck.test"),
        MessageDraft(chat_key="chat1", chat_name="Chat", sender="Ada", text="hello chat", ts=_TS),
        TransactionDraft(
            amount_milliunits=-4500, payee="Cafe", title="Cafe", text="coffee memo", ts=_TS
        ),
        LocationDraft(lat=48.85, lon=2.35, title="Paris visit", ts=_TS),
        PhotoDraft(title="pic.jpg", text="a photo", size_bytes=5, sha256="cd" * 32, ts=_TS),
        NoteDraft(title="note title", text="unmistakabletoken note body"),
    ]


def _ingest_mixed(ctx: AppContext, *, source_name: str = "mixed-src") -> int:
    return run_import(
        ctx.db,
        source_name=source_name,
        parser_version=1,
        drafts=iter(_mixed_drafts()),
        path="/tmp/mixed.zip",
        file_hash="f" * 64,
    )


def _count(ctx: AppContext, sql: str, params: tuple[object, ...] = ()) -> int:
    with ctx.db.read() as conn:
        return int(conn.execute(sql, params).fetchone()[0])


def _assert_no_orphans(ctx: AppContext) -> None:
    """Zero satellite/files rows for missing items, FTS in sync, FKs clean."""
    with ctx.db.read() as conn:
        for table in _CHILD_TABLES:
            orphans = conn.execute(
                f"SELECT COUNT(*) FROM {table} "  # noqa: S608 — table names are a fixed tuple
                "WHERE item_id NOT IN (SELECT id FROM items)"
            ).fetchone()[0]
            assert orphans == 0, f"{table} has {orphans} orphan row(s)"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    ctx.db.write(integrity_check)


def _fts_hits(ctx: AppContext, query: str) -> int:
    with ctx.db.read() as conn:
        rows = conn.execute(
            "SELECT rowid FROM items_fts WHERE items_fts MATCH ?", (query,)
        ).fetchall()
    return len(rows)


# ---------------------------------------------------------------------------
# rm --import
# ---------------------------------------------------------------------------


def test_remove_import_deletes_items_satellites_fts_and_ledger_row(ctx: AppContext) -> None:
    import_id = _ingest_mixed(ctx)
    assert _fts_hits(ctx, "unmistakabletoken") == 1

    result = lifecycle_service.remove_import(ctx, import_id)

    assert result.items_deleted == 7
    assert result.imports_deleted == 1
    assert result.hashes_suppressed == 0
    assert _count(ctx, "SELECT COUNT(*) FROM items") == 0
    assert _count(ctx, "SELECT COUNT(*) FROM imports") == 0
    assert _fts_hits(ctx, "unmistakabletoken") == 0
    _assert_no_orphans(ctx)


def test_remove_import_unknown_id(ctx: AppContext) -> None:
    with pytest.raises(ImportNotFoundError):
        lifecycle_service.remove_import(ctx, 999)


def test_remove_import_refuses_running(ctx: AppContext) -> None:
    def _seed(conn: sqlite3.Connection) -> int:
        return insert_import(conn, insert_source(conn))  # status defaults to 'running'

    import_id = ctx.db.write(_seed)
    with pytest.raises(ImportRunningError):
        lifecycle_service.remove_import(ctx, import_id)
    assert _count(ctx, "SELECT COUNT(*) FROM imports") == 1


def test_remove_import_kills_ledger_short_circuit(ctx: AppContext, tmp_path: Path) -> None:
    """Deleting the imports row means re-importing the same archive re-ingests
    (the #126 completed-run short-circuit no longer matches)."""
    archive = write_keep_takeout(tmp_path / "keep", 8, seed=3, fmt="zip")
    [first] = import_path(ctx, archive)
    assert first.items_new > 0

    lifecycle_service.remove_import(ctx, first.id)
    assert _count(ctx, "SELECT COUNT(*) FROM items") == 0

    [second] = import_path(ctx, archive)
    # Genuinely re-ingested (items_new, not a returned prior completed run) —
    # note SQLite may reuse the deleted row's id for the fresh run.
    assert second.items_new == first.items_new
    assert second.items_duplicate == first.items_duplicate
    assert _count(ctx, "SELECT COUNT(*) FROM items") == first.items_new
    _assert_no_orphans(ctx)


def test_remove_import_unlinks_children_in_other_imports(ctx: AppContext) -> None:
    """An email reply in import 2 links (parent_id) to a parent in import 1;
    deleting import 1 must null the link, not raise an FK error."""
    first = run_import(
        ctx.db,
        source_name="gmail-test",
        parser_version=1,
        drafts=iter([email_draft(1)]),
        path="/tmp/part1.mbox",
        file_hash="a" * 64,
    )
    run_import(
        ctx.db,
        source_name="gmail-test",
        parser_version=1,
        drafts=iter([email_draft(2, in_reply_to="m1@potluck.test")]),
        path="/tmp/part2.mbox",
        file_hash="b" * 64,
    )
    assert _count(ctx, "SELECT COUNT(*) FROM items WHERE parent_id IS NOT NULL") == 1

    result = lifecycle_service.remove_import(ctx, first)

    assert result.items_deleted == 1
    assert _count(ctx, "SELECT COUNT(*) FROM items") == 1
    assert _count(ctx, "SELECT COUNT(*) FROM items WHERE parent_id IS NOT NULL") == 0
    _assert_no_orphans(ctx)


# ---------------------------------------------------------------------------
# rm --source
# ---------------------------------------------------------------------------


def test_remove_source_deletes_items_and_history_keeps_sources_row(ctx: AppContext) -> None:
    _ingest_mixed(ctx, source_name="doomed-src")
    _ingest_mixed(ctx, source_name="kept-src")

    result = lifecycle_service.remove_source(ctx, "doomed-src")

    assert result.items_deleted == 7
    assert result.imports_deleted == 1
    assert _count(ctx, "SELECT COUNT(*) FROM items") == 7
    assert _count(ctx, "SELECT COUNT(*) FROM imports") == 1
    # The sources row survives (stable ids elsewhere).
    assert _count(ctx, "SELECT COUNT(*) FROM sources WHERE name = 'doomed-src'") == 1
    _assert_no_orphans(ctx)


def test_remove_source_unknown_name(ctx: AppContext) -> None:
    with pytest.raises(SourceNotFoundError):
        lifecycle_service.remove_source(ctx, "never-imported")


def test_remove_source_refuses_running(ctx: AppContext) -> None:
    def _seed(conn: sqlite3.Connection) -> None:
        insert_import(conn, insert_source(conn, "busy-src"))

    ctx.db.write(_seed)
    with pytest.raises(ImportRunningError):
        lifecycle_service.remove_source(ctx, "busy-src")


def test_remove_source_reimport_restores(ctx: AppContext, tmp_path: Path) -> None:
    archive = write_keep_takeout(tmp_path / "keep", 8, seed=3, fmt="zip")
    [first] = import_path(ctx, archive)

    lifecycle_service.remove_source(ctx, "google_keep")
    assert _count(ctx, "SELECT COUNT(*) FROM items") == 0
    assert _count(ctx, "SELECT COUNT(*) FROM imports") == 0

    [second] = import_path(ctx, archive)
    assert second.items_new == first.items_new


# ---------------------------------------------------------------------------
# rm <item-id...>
# ---------------------------------------------------------------------------


def test_remove_items_deletes_rows_and_clears_owning_file_hash(ctx: AppContext) -> None:
    """Plain rm of items clears file_hash on the owning imports row so the
    short-circuit cannot silently keep the removed item gone forever."""
    import_id = _ingest_mixed(ctx)
    with ctx.db.read() as conn:
        ids = [int(r[0]) for r in conn.execute("SELECT id FROM items ORDER BY id").fetchall()]

    result = lifecycle_service.remove_items(ctx, ids[:2])

    assert result.items_deleted == 2
    assert result.imports_deleted == 0
    assert _count(ctx, "SELECT COUNT(*) FROM items") == 5
    with ctx.db.read() as conn:
        row = conn.execute("SELECT file_hash FROM imports WHERE id = ?", (import_id,)).fetchone()
    assert row[0] is None
    _assert_no_orphans(ctx)


def test_remove_items_unknown_id_is_atomic(ctx: AppContext) -> None:
    _ingest_mixed(ctx)
    with ctx.db.read() as conn:
        first_id = int(conn.execute("SELECT MIN(id) FROM items").fetchone()[0])

    with pytest.raises(ItemNotFoundError):
        lifecycle_service.remove_items(ctx, [first_id, 99_999])
    # Nothing was deleted — the whole call refused.
    assert _count(ctx, "SELECT COUNT(*) FROM items") == 7


def test_remove_items_unlinks_replies_to_deleted_parent(ctx: AppContext) -> None:
    run_import(
        ctx.db,
        source_name="gmail-test",
        parser_version=1,
        drafts=iter([email_draft(1), email_draft(2, in_reply_to="m1@potluck.test")]),
        path="/tmp/t.mbox",
        file_hash=None,
    )
    with ctx.db.read() as conn:
        parent_id = int(
            conn.execute(
                "SELECT item_id FROM emails WHERE message_id = 'm1@potluck.test'"
            ).fetchone()[0]
        )

    lifecycle_service.remove_items(ctx, [parent_id])

    assert _count(ctx, "SELECT COUNT(*) FROM items") == 1
    assert _count(ctx, "SELECT COUNT(*) FROM items WHERE parent_id IS NOT NULL") == 0
    _assert_no_orphans(ctx)


def test_remove_items_refuses_running_owner(ctx: AppContext) -> None:
    def _seed(conn: sqlite3.Connection) -> int:
        source_id = insert_source(conn)
        import_id = insert_import(conn, source_id)  # status 'running'
        return insert_item(conn, source_id, import_id, content_hash="h1")

    item_id = ctx.db.write(_seed)
    with pytest.raises(ImportRunningError):
        lifecycle_service.remove_items(ctx, [item_id])
    assert _count(ctx, "SELECT COUNT(*) FROM items") == 1


def test_remove_items_chunks_ids_under_the_sqlite_variable_limit(ctx: AppContext) -> None:
    """A >1000-id delete must not expand into one placeholder list — proven
    by pinning SQLITE_LIMIT_VARIABLE_NUMBER to its historical 999 default on
    the write connection (task-11 review M3)."""

    def _seed(conn: sqlite3.Connection) -> list[int]:
        conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        source_id = insert_source(conn)
        import_id = insert_import(conn, source_id)
        conn.execute("UPDATE imports SET status = 'completed' WHERE id = ?", (import_id,))
        return [
            insert_item(conn, source_id, import_id, content_hash=f"chunk-h{i}") for i in range(1200)
        ]

    ids = ctx.db.write(_seed)
    result = lifecycle_service.remove_items(ctx, ids, forget=True)
    assert result.items_deleted == 1200
    assert result.hashes_suppressed == 1200
    assert _count(ctx, "SELECT COUNT(*) FROM items") == 0
    _assert_no_orphans(ctx)


# ---------------------------------------------------------------------------
# forget: everything rm does + suppressed_hashes
# ---------------------------------------------------------------------------


def test_forget_import_blocks_reimport(ctx: AppContext, tmp_path: Path) -> None:
    """The brief's golden-fixture acceptance: import → forget → re-import same
    archive → items absent, suppressed counter incremented."""
    archive = write_keep_takeout(tmp_path / "keep", 8, seed=3, fmt="zip")
    [first] = import_path(ctx, archive)
    count = first.items_new

    result = lifecycle_service.remove_import(ctx, first.id, forget=True)

    assert result.hashes_suppressed == count
    assert _count(ctx, "SELECT COUNT(*) FROM suppressed_hashes") == count

    [second] = import_path(ctx, archive)
    assert second.items_new == 0
    # Archive-internal duplicate drafts (if any) still count duplicate — the
    # first occurrence of each suppressed hash counts suppressed.
    assert second.items_duplicate == first.items_duplicate
    assert second.items_suppressed == count
    assert _count(ctx, "SELECT COUNT(*) FROM items") == 0
    _assert_no_orphans(ctx)


def test_forget_single_item_only_blocks_that_item(ctx: AppContext, tmp_path: Path) -> None:
    archive = write_keep_takeout(tmp_path / "keep", 8, seed=3, fmt="zip")
    [first] = import_path(ctx, archive)
    count = first.items_new
    with ctx.db.read() as conn:
        victim = int(conn.execute("SELECT MIN(id) FROM items").fetchone()[0])

    result = lifecycle_service.remove_items(ctx, [victim], forget=True)
    assert result.hashes_suppressed == 1

    [second] = import_path(ctx, archive)
    assert second.items_new == 0
    assert second.items_suppressed == 1
    assert second.items_duplicate == first.items_duplicate + count - 1
    assert _count(ctx, "SELECT COUNT(*) FROM items") == count - 1
    assert _count(ctx, "SELECT COUNT(*) FROM items WHERE id = ?", (victim,)) == 0


def test_forget_source_suppresses_all_hashes(ctx: AppContext) -> None:
    _ingest_mixed(ctx, source_name="doomed-src")

    result = lifecycle_service.remove_source(ctx, "doomed-src", forget=True)

    assert result.items_deleted == 7
    assert result.hashes_suppressed == 7
    assert _count(ctx, "SELECT COUNT(*) FROM suppressed_hashes") == 7


def test_forget_already_suppressed_hash_not_recounted(ctx: AppContext) -> None:
    """The same content under two sources shares a content hash; the second
    forget finds it already suppressed (INSERT OR IGNORE) and counts 0."""

    def _seed(conn: sqlite3.Connection) -> tuple[int, int]:
        src_a = insert_source(conn, "src-a")
        src_b = insert_source(conn, "src-b")
        import_a = insert_import(conn, src_a)
        import_b = insert_import(conn, src_b)
        conn.execute("UPDATE imports SET status = 'completed'")
        item_a = insert_item(conn, src_a, import_a, content_hash="samehash")
        item_b = insert_item(conn, src_b, import_b, content_hash="samehash")
        return item_a, item_b

    item_a, item_b = ctx.db.write(_seed)

    first = lifecycle_service.remove_items(ctx, [item_a], forget=True)
    second = lifecycle_service.remove_items(ctx, [item_b], forget=True)

    assert first.hashes_suppressed == 1
    assert second.hashes_suppressed == 0
    assert _count(ctx, "SELECT COUNT(*) FROM suppressed_hashes") == 1


# ---------------------------------------------------------------------------
# Engine: batch anti-join against suppressed_hashes
# ---------------------------------------------------------------------------


def test_engine_drops_suppressed_drafts_into_dedicated_counter(ctx: AppContext) -> None:
    """A suppressed hash never re-ingests; it counts as items_suppressed —
    never folded into skipped or duplicate."""
    drafts = [NoteDraft(title=f"t{n}", text=f"body {n}") for n in range(3)]
    banned = content_hash(drafts[1])

    def _suppress(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO suppressed_hashes (content_hash, suppressed_at) VALUES (?, ?)",
            (banned, "2026-01-01T00:00:00+00:00"),
        )

    ctx.db.write(_suppress)

    import_id = run_import(
        ctx.db,
        source_name="notes-src",
        parser_version=1,
        drafts=iter(drafts),
        path="/tmp/notes.zip",
        file_hash=None,
    )

    with ctx.db.read() as conn:
        row = conn.execute(
            """SELECT items_new, items_duplicate, items_skipped, items_suppressed
               FROM imports WHERE id = ?""",
            (import_id,),
        ).fetchone()
    assert tuple(row) == (2, 0, 0, 1)
    assert _count(ctx, "SELECT COUNT(*) FROM items") == 2
    assert _count(ctx, "SELECT COUNT(*) FROM items WHERE title = 't1'") == 0


def test_import_run_dto_surfaces_suppressed_in_items_done(ctx: AppContext) -> None:
    from potluck.models.imports import ImportRun

    run = ImportRun(
        id=1,
        source="s",
        path="/tmp/x",
        file_hash=None,
        parser_version=1,
        started_at=_TS,
        finished_at=None,
        status="completed",
        items_new=2,
        items_duplicate=1,
        items_updated=0,
        items_skipped=0,
        items_suppressed=3,
        error=None,
    )
    assert run.items_done == 6


# ---------------------------------------------------------------------------
# Single-transaction observability
# ---------------------------------------------------------------------------


def test_delete_is_one_transaction_never_partially_visible(ctx: AppContext) -> None:
    """A concurrent reader polling during the delete only ever sees the full
    corpus or nothing — no half-deleted state (one BEGIN..COMMIT)."""
    count = 3000
    drafts = [NoteDraft(title=f"n{i}", text=f"body {i}") for i in range(count)]
    import_id = run_import(
        ctx.db,
        source_name="bulk-src",
        parser_version=1,
        drafts=iter(drafts),
        path="/tmp/bulk.zip",
        file_hash=None,
    )

    samples: list[tuple[int, int]] = []
    stop = threading.Event()

    def _poll() -> None:
        # Reader thread gets its own query-only connection (per-thread cache).
        # BOTH counts come from ONE statement — a single WAL snapshot — so a
        # commit between two separate reads can never fake a partial state.
        while not stop.is_set():
            with ctx.db.read() as conn:
                row = conn.execute(
                    "SELECT (SELECT COUNT(*) FROM items), (SELECT COUNT(*) FROM imports)"
                ).fetchone()
            samples.append((int(row[0]), int(row[1])))
            time.sleep(0.001)

    poller = threading.Thread(target=_poll)
    poller.start()
    try:
        lifecycle_service.remove_import(ctx, import_id)
    finally:
        stop.set()
        poller.join(timeout=10)

    assert samples, "poller never sampled"
    allowed = {(count, 1), (0, 0)}
    assert set(samples) <= allowed, f"partial states observed: {set(samples) - allowed}"
