"""Attachment handling (#124): files rows, ParseContext, content-addressed store."""

from datetime import UTC, datetime
from pathlib import Path

from potluck.ingest.attachments import AttachmentStore
from potluck.ingest.engine import run_import
from potluck.ingest.plugins import ParseContext
from potluck.models.drafts import EmailAttachment, EmailDraft, ItemDraft
from potluck.services.context import AppContext

SHA_A = "aa" * 32
SHA_B = "bb" * 32


def _email(n: int, attachments: tuple[EmailAttachment, ...]) -> EmailDraft:
    msgid = f"m{n}@potluck.test"
    return EmailDraft(
        external_id=f"mid:{msgid}",
        message_id=msgid,
        thread_key=msgid,
        title=f"subject {n}",
        text=f"body {n}",
        ts=datetime(2024, 1, 1, tzinfo=UTC),
        attachments=attachments,
    )


def _att(sha: str, *, filename: str | None = "doc.pdf", size: int = 10) -> EmailAttachment:
    return EmailAttachment(filename=filename, mime="application/pdf", size_bytes=size, sha256=sha)


def _run(ctx: AppContext, drafts: list[ItemDraft]) -> int:
    return run_import(
        ctx.db,
        source_name="gmail-test",
        parser_version=1,
        drafts=iter(drafts),
        path="/tmp/t.mbox",
        file_hash=None,
    )


# ---------------------------------------------------------------------------
# ParseContext
# ---------------------------------------------------------------------------


def test_parse_context_defaults() -> None:
    parse_ctx = ParseContext()
    assert parse_ctx.attachments_dir is None


# ---------------------------------------------------------------------------
# files rows via the email satellite writer
# ---------------------------------------------------------------------------


def test_attachment_metadata_lands_in_files(ctx: AppContext) -> None:
    _run(ctx, [_email(1, (_att(SHA_A), _att(SHA_B, filename=None)))])
    with ctx.db.read() as conn:
        rows = conn.execute(
            "SELECT member_path, mime, size_bytes, sha256 FROM files ORDER BY id"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["member_path"] == "doc.pdf"
    assert rows[0]["mime"] == "application/pdf"
    assert rows[0]["size_bytes"] == 10
    assert rows[0]["sha256"] == SHA_A
    # unnamed attachments get a positional member_path
    assert rows[1]["member_path"] == "part-2"
    assert rows[1]["sha256"] == SHA_B


def test_no_attachments_no_files_rows(ctx: AppContext) -> None:
    _run(ctx, [_email(1, ())])
    with ctx.db.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    assert count == 0


def test_reimport_with_changed_attachments_replaces_rows(ctx: AppContext) -> None:
    _run(ctx, [_email(1, (_att(SHA_A),))])
    _run(ctx, [_email(1, (_att(SHA_B),))])
    with ctx.db.read() as conn:
        shas = [str(r[0]) for r in conn.execute("SELECT sha256 FROM files").fetchall()]
    assert shas == [SHA_B]


def test_same_attachment_across_messages_keeps_per_message_rows(ctx: AppContext) -> None:
    """Metadata stays per (message, attachment); dedup happens at blob level."""
    _run(ctx, [_email(1, (_att(SHA_A),)), _email(2, (_att(SHA_A),))])
    with ctx.db.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM files WHERE sha256 = ?", (SHA_A,)).fetchone()[0]
    assert count == 2


# ---------------------------------------------------------------------------
# AttachmentStore: content-addressed blob extraction
# ---------------------------------------------------------------------------


def test_store_writes_content_addressed(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / "att")
    path = store.save(SHA_A, b"payload bytes")
    assert path == tmp_path / "att" / SHA_A[:2] / SHA_A
    assert path.read_bytes() == b"payload bytes"


def test_store_dedups_by_hash(tmp_path: Path) -> None:
    """The second message carrying the same blob is a free no-op."""
    store = AttachmentStore(tmp_path / "att")
    first = store.save(SHA_A, b"payload bytes")
    stamp = first.stat().st_mtime_ns
    second = store.save(SHA_A, b"payload bytes")
    assert second == first
    assert first.stat().st_mtime_ns == stamp
    blobs = [p for p in (tmp_path / "att").rglob("*") if p.is_file()]
    assert len(blobs) == 1
