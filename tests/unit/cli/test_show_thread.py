"""CLI `potluck show --thread` (#123)."""

import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from potluck.cli.app import app
from potluck.ingest.engine import run_import
from potluck.models.drafts import EmailDraft
from potluck.services.context import create_context

runner = CliRunner()


def _ingest_thread() -> int:
    """Ingest a three-message conversation into the isolated env's database;
    returns the middle message's item id."""
    drafts = [
        EmailDraft(
            external_id=f"mid:m{n}@potluck.test",
            message_id=f"m{n}@potluck.test",
            in_reply_to=f"m{n - 1}@potluck.test" if n > 1 else None,
            thread_key="m1@potluck.test",
            from_addr=f"sender{n}@potluck.test",
            title=f"subject {n}",
            text=f"body {n}",
            ts=datetime(2024, 1, n, tzinfo=UTC),
        )
        for n in (1, 2, 3)
    ]
    ctx = create_context()
    try:
        run_import(
            ctx.db,
            source_name="gmail-test",
            parser_version=1,
            drafts=iter(drafts),
            path="/tmp/t.mbox",
            file_hash=None,
        )
        with ctx.db.read() as conn:
            row = conn.execute(
                "SELECT item_id FROM emails WHERE message_id = 'm2@potluck.test'"
            ).fetchone()
        return int(row[0])
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
