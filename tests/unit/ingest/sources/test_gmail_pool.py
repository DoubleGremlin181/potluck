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
