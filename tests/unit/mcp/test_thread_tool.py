"""MCP get_thread tool (#123): conversation retrieval parity with the service."""

from datetime import UTC, datetime

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from potluck.ingest.engine import run_import
from potluck.mcp.server import create_mcp
from potluck.models.drafts import EmailDraft
from potluck.services.context import AppContext


def _ingest_thread(ctx: AppContext) -> int:
    """Ingest a two-message conversation; returns the reply's item id."""
    drafts = [
        EmailDraft(
            external_id="mid:m1@potluck.test",
            message_id="m1@potluck.test",
            thread_key="m1@potluck.test",
            from_addr="alice@potluck.test",
            title="garden plans",
            text="first message",
            ts=datetime(2024, 1, 1, tzinfo=UTC),
        ),
        EmailDraft(
            external_id="mid:m2@potluck.test",
            message_id="m2@potluck.test",
            in_reply_to="m1@potluck.test",
            thread_key="m1@potluck.test",
            from_addr="bob@potluck.test",
            title="Re: garden plans",
            text="the reply",
            ts=datetime(2024, 1, 2, tzinfo=UTC),
        ),
    ]
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


async def test_get_thread_returns_conversation(ctx: AppContext) -> None:
    reply_id = _ingest_thread(ctx)
    async with Client(create_mcp(ctx)) as client:
        result = await client.call_tool("get_thread", {"item_id": reply_id})
    data = result.data
    assert data.item_id == reply_id
    assert data.thread_key == "m1@potluck.test"
    titles = [e.title for e in data.entries]
    assert titles == ["garden plans", "Re: garden plans"]


async def test_get_thread_unknown_id_is_tool_error(ctx: AppContext) -> None:
    async with Client(create_mcp(ctx)) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_thread", {"item_id": 424242})
