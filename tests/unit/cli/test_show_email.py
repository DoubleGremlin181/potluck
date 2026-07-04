"""CLI `potluck show` surfaces email satellite detail (#200)."""

import json

from typer.testing import CliRunner

from potluck.cli.app import app
from potluck.models.drafts import EmailAttachment
from potluck.services.context import create_context
from tests.conftest import email_draft, email_item_id, ingest_email_drafts

runner = CliRunner()


def _ingest_email() -> int:
    draft = email_draft(
        1,
        title="garden notes",
        text="body",
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
        ingest_email_drafts(ctx, draft)
        return email_item_id(ctx, "m1@potluck.test")
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
        ingest_email_drafts(
            ctx, NoteDraft(title="t", text="x"), source_name="keep-test", path="/tmp/t.zip"
        )
        with ctx.db.read() as conn:
            item_id = int(conn.execute("SELECT id FROM items").fetchone()[0])
    finally:
        ctx.db.close()
    result = runner.invoke(app, ["show", str(item_id), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["email"] is None
