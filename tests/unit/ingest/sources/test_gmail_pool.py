"""Process-pool MIME parsing (#199): identical output, contained failures.

Rule-3 evidence (the speedup itself) lives in the bench tier; these tests pin
the correctness contract — pooled parsing must be byte-for-byte equivalent to
sequential parsing, in draft order and in the resulting database.
"""

import os
import sqlite3
from pathlib import Path

from potluck.core.config import Settings
from potluck.ingest.plugins import ParseContext
from potluck.ingest.readers import open_archive
from potluck.ingest.sources.gmail import _effective_workers, parse
from potluck.models.drafts import EmailDraft
from potluck.services.context import create_context
from potluck.services.imports import import_path
from potluck.testing.mbox import write_gmail_takeout


def test_settings_ingest_workers_defaults_to_auto() -> None:
    assert Settings().ingest_workers == 0


def test_effective_workers_resolution() -> None:
    assert _effective_workers(0) == min(4, os.cpu_count() or 1)
    assert _effective_workers(1) == 1
    assert _effective_workers(3) == 3


def test_pooled_drafts_match_sequential(tmp_path: Path) -> None:
    """Order-preserving imap: same drafts, same order — including #N msgid
    suffixes, which depend on sequential bookkeeping in the parent."""
    archive_path = write_gmail_takeout(tmp_path / "takeout", 40, seed=7)
    sequential = list(parse(open_archive(archive_path), ParseContext(workers=1)))
    pooled = list(parse(open_archive(archive_path), ParseContext(workers=4)))
    assert pooled == sequential
    assert len(pooled) == 40


def _dump(db_path: Path) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(db_path)
    try:
        rows: list[tuple[object, ...]] = []
        for sql in (
            "SELECT id, external_id, content_hash, kind, ts, title, text FROM items ORDER BY id",
            "SELECT * FROM emails ORDER BY item_id",
            "SELECT item_id, member_path, mime, size_bytes, sha256 FROM files"
            " ORDER BY item_id, member_path",
        ):
            rows.extend(tuple(r) for r in conn.execute(sql).fetchall())
        return rows
    finally:
        conn.close()


def test_pooled_import_identical_db(tmp_path: Path) -> None:
    """End-to-end through Settings -> ParseContext -> engine: the pooled and
    sequential imports of one archive produce identical logical databases."""
    archive_path = write_gmail_takeout(tmp_path / "takeout", 30, seed=7)

    dumps: list[list[tuple[object, ...]]] = []
    for name, workers in (("seq", 1), ("pool", 4)):
        db_path = tmp_path / f"{name}.db"
        ctx = create_context(Settings(db_path=db_path, ingest_workers=workers))
        try:
            [run] = import_path(ctx, archive_path)
            assert run.items_new == 30
        finally:
            ctx.db.close()
        dumps.append(_dump(db_path))
    assert dumps[0] == dumps[1]


def test_pooled_attachment_extraction(tmp_path: Path) -> None:
    """Workers write blobs themselves; the content-addressed store ends up
    with the same files as a sequential run."""
    archive_path = write_gmail_takeout(tmp_path / "takeout", 20, seed=7)

    blob_sets: list[set[str]] = []
    for name, workers in (("seq", 1), ("pool", 2)):
        attachments_dir = tmp_path / f"blobs-{name}"
        drafts = list(
            parse(
                open_archive(archive_path),
                ParseContext(attachments_dir=attachments_dir, workers=workers),
            )
        )
        assert any(d.attachments for d in drafts if isinstance(d, EmailDraft))
        blob_sets.append({p.name for p in attachments_dir.rglob("*") if p.is_file()})
    assert blob_sets[0] == blob_sets[1]
    assert blob_sets[0], "expected extracted attachment blobs"


# ---------------------------------------------------------------------------
# attachment-store I/O failures fail the run (#198 review 3)
# ---------------------------------------------------------------------------


def test_sequential_store_failure_propagates(tmp_path: Path, monkeypatch: object) -> None:
    """ENOSPC while writing a blob must abort parse(), not skip the email."""
    import errno

    import pytest

    from potluck.ingest.attachments import AttachmentStore

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    archive_path = write_gmail_takeout(tmp_path / "takeout", 20, seed=7)

    def exploding_save(self: AttachmentStore, sha256: str, payload: bytes) -> Path:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(AttachmentStore, "save", exploding_save)
    with pytest.raises(OSError, match="No space left"):
        list(
            parse(
                open_archive(archive_path),
                ParseContext(attachments_dir=tmp_path / "blobs", workers=1),
            )
        )


def test_pooled_store_failure_propagates(tmp_path: Path) -> None:
    """Worker-side blob-write failures must surface in the parent, not be
    swallowed as 'unparseable message'. The attachments dir's parent is an
    existing FILE, so mkdir raises inside real spawn workers."""
    import pytest

    archive_path = write_gmail_takeout(tmp_path / "takeout", 20, seed=7)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")

    with pytest.raises(OSError):
        list(
            parse(
                open_archive(archive_path),
                ParseContext(attachments_dir=blocker / "blobs", workers=2),
            )
        )


def test_import_failure_marks_run_failed(tmp_path: Path, monkeypatch: object) -> None:
    """End-to-end: a blob-write failure leaves the ledger row 'failed', so a
    re-import after fixing the disk re-parses instead of short-circuiting."""
    import errno

    import pytest

    from potluck.ingest.attachments import AttachmentStore
    from potluck.services.imports import list_imports

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    archive_path = write_gmail_takeout(tmp_path / "takeout", 20, seed=7)

    def exploding_save(self: AttachmentStore, sha256: str, payload: bytes) -> Path:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(AttachmentStore, "save", exploding_save)
    ctx = create_context(
        Settings(db_path=tmp_path / "t.db", extract_attachments=True, ingest_workers=1)
    )
    try:
        with pytest.raises(OSError):
            import_path(ctx, archive_path)
        runs = list_imports(ctx).runs
        assert runs and runs[0].status == "failed"
    finally:
        ctx.db.close()


# ---------------------------------------------------------------------------
# bounded in-flight window (#198 review 13)
# ---------------------------------------------------------------------------


def test_pool_inflight_window_bounded(tmp_path: Path, monkeypatch: object) -> None:
    """The parent never holds more than _MAX_INFLIGHT_CHUNKS results pending,
    so RSS stays bounded even when the consumer is slower than the workers."""
    import pytest

    from potluck.ingest.sources import gmail as gmail_mod

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    archive_path = write_gmail_takeout(tmp_path / "takeout", 100, seed=7)

    class FakePool:
        """In-process pool double that records outstanding-result pressure."""

        def __init__(self) -> None:
            self.outstanding = 0
            self.max_outstanding = 0

        def apply_async(self, fn: object, args: tuple[object, ...]) -> object:
            assert callable(fn)
            self.outstanding += 1
            self.max_outstanding = max(self.max_outstanding, self.outstanding)
            value = fn(*args)
            pool = self

            class Result:
                def get(self) -> object:
                    pool.outstanding -= 1
                    return value

            return Result()

        def __enter__(self) -> "FakePool":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    fake_pool = FakePool()

    class FakeContext:
        def Pool(  # noqa: N802 — mirrors multiprocessing.context.BaseContext.Pool
            self, workers: int, initializer: object = None, initargs: tuple[object, ...] = ()
        ) -> FakePool:
            if callable(initializer):
                initializer(*initargs)
            return fake_pool

    monkeypatch.setattr(gmail_mod, "_POOL_CHUNKSIZE", 4)
    monkeypatch.setattr(gmail_mod, "_MAX_INFLIGHT_CHUNKS", 3)
    import types

    monkeypatch.setattr(
        gmail_mod,
        "multiprocessing",
        types.SimpleNamespace(get_context=lambda method: FakeContext()),
    )

    pooled = list(parse(open_archive(archive_path), ParseContext(workers=2)))
    sequential = list(parse(open_archive(archive_path), ParseContext(workers=1)))
    assert pooled == sequential
    assert fake_pool.max_outstanding <= 3


def test_pooled_window_cycling_preserves_order(tmp_path: Path) -> None:
    """More chunks than the in-flight window: every draft still arrives, in
    sequential order (real spawn pool)."""
    archive_path = write_gmail_takeout(tmp_path / "takeout", 600, seed=7)
    sequential = list(parse(open_archive(archive_path), ParseContext(workers=1)))
    pooled = list(parse(open_archive(archive_path), ParseContext(workers=2)))
    assert pooled == sequential
    assert len(pooled) == 600
