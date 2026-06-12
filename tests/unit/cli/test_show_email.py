"""CLI `potluck show` surfaces email satellite detail (#200)."""

import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from potluck.cli.app import app
from potluck.ingest.engine import run_import
from potluck.models.drafts import EmailAttachment, EmailDraft
from potluck.services.context import create_context

runner = CliRunner()


def _ingest_email() -> int:
    draft = EmailDraft(
        external_id="mid:m1@potluck.test",
        message_id="m1@potluck.test",
        thread_key="m1@potluck.test",
        title="garden notes",
        text="body",
        ts=datetime(2024, 1, 1, tzinfo=UTC),
        from_addr="alice@potluck.test",
        from_name="Alice A",
        to_addrs=("bob@potluck.test",),
        to_names=("Bob B",),
        bcc_addrs=("dave@potluck.test",),
        labels=("Inbox",),
        attachments=(
            EmailAttachment(filename="map.png", mime="image/png", size_bytes=5, sha256="ab" * 32),
        ),
    )
    ctx = create_context()
    try:
        run_import(
            ctx.db,
            source_name="gmail-test",
            parser_version=1,
            drafts=iter([draft]),
            path="/tmp/t.mbox",
            file_hash=None,
        )
        with ctx.db.read() as conn:
            return int(conn.execute("SELECT id FROM items").fetchone()[0])
    finally:
        ctx.db.close()


def test_show_email_renders_satellite_rows() -> None:
    item_id = _ingest_email()
    result = runner.invoke(app, ["show", str(item_id)])
    assert result.exit_code == 0, result.output
    assert "Alice A" in result.output
    assert "alice@potluck.test" in result.output
    assert "bob@potluck.test" in result.output
    assert "dave@potluck.test" in result.output  # bcc
    assert "Inbox" in result.output
    assert "map.png" in result.output
    assert "m1@potluck.test" in result.output


def test_show_email_json_includes_detail() -> None:
    item_id = _ingest_email()
    result = runner.invoke(app, ["show", str(item_id), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["email"]["from_name"] == "Alice A"
    assert data["email"]["attachments"][0]["filename"] == "map.png"


def test_show_note_json_email_is_null() -> None:
    from potluck.models.drafts import NoteDraft

    ctx = create_context()
    try:
        run_import(
            ctx.db,
            source_name="keep-test",
            parser_version=1,
            drafts=iter([NoteDraft(title="t", text="x")]),
            path="/tmp/t.zip",
            file_hash=None,
        )
        with ctx.db.read() as conn:
            item_id = int(conn.execute("SELECT id FROM items").fetchone()[0])
    finally:
        ctx.db.close()
    result = runner.invoke(app, ["show", str(item_id), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["email"] is None
