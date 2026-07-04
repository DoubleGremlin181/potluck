"""MCP get_thread tool (#123): conversation retrieval parity with the service."""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from potluck.mcp.server import create_mcp
from potluck.services.context import AppContext
from tests.conftest import email_draft, email_item_id, ingest_email_drafts


def _ingest_thread(ctx: AppContext) -> int:
    """Ingest a two-message conversation; returns the reply's item id."""
    ingest_email_drafts(
        ctx,
        email_draft(1, title="garden plans", text="first message", from_addr="alice@potluck.test"),
        email_draft(
            2,
            thread_key="m1@potluck.test",
            in_reply_to="m1@potluck.test",
            title="Re: garden plans",
            text="the reply",
            from_addr="bob@potluck.test",
        ),
    )
    return email_item_id(ctx, "m2@potluck.test")


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
