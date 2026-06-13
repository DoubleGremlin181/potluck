"""CLI `potluck show --thread` (#123)."""

import json

from typer.testing import CliRunner

from potluck.cli.app import app
from potluck.services.context import create_context
from tests.conftest import email_draft, email_item_id, ingest_email_drafts

runner = CliRunner()


def _ingest_thread() -> int:
    """Ingest a three-message conversation into the isolated env's database;
    returns the middle message's item id."""
    drafts = [
        email_draft(
            n,
            thread_key="m1@potluck.test",
            in_reply_to=f"m{n - 1}@potluck.test" if n > 1 else None,
        )
        for n in (1, 2, 3)
    ]
    ctx = create_context()
    try:
        ingest_email_drafts(ctx, *drafts)
        return email_item_id(ctx, "m2@potluck.test")
    finally:
        ctx.db.close()


def test_show_thread_lists_conversation() -> None:
    anchor = _ingest_thread()
    result = runner.invoke(app, ["show", str(anchor), "--thread"])
    assert result.exit_code == 0, result.output
    assert "subject 1" in result.output
    assert "subject 2" in result.output
    assert "subject 3" in result.output
    assert "sender1@potluck.test" in result.output


def test_show_thread_json() -> None:
    anchor = _ingest_thread()
    result = runner.invoke(app, ["show", str(anchor), "--thread", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["item_id"] == anchor
    assert [e["title"] for e in data["entries"]] == ["subject 1", "subject 2", "subject 3"]


def test_show_thread_unknown_id_fails() -> None:
    result = runner.invoke(app, ["show", "424242", "--thread"])
    assert result.exit_code == 1
