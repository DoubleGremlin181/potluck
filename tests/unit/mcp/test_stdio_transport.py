"""Real stdio transport end-to-end (#139).

The docs promise that pointing a stdio MCP client at ``potluck mcp`` just
works — this test keeps that promise honest by spawning the real CLI as a
subprocess (``python -m potluck mcp``, the same wire path Claude Desktop and
Claude Code use), then listing tools and searching a populated corpus through
a real MCP client. The in-process tests in test_server.py cover tool
behaviour; this one covers the transport.
"""

import os
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from potluck.services.context import AppContext
from tests.conftest import ingest_keep_corpus

EXPECTED_TOOLS = {"search", "get_item", "get_thread", "list_items", "list_sources", "get_stats"}


async def test_stdio_subprocess_lists_tools_and_searches(ctx: AppContext, tmp_path: Path) -> None:
    """A stdio MCP client spawning the real CLI lists the v1 toolset and gets
    ranked, snippeted hits back — proving stdout carries only protocol frames
    (any stray print would corrupt the session and fail the handshake)."""
    ingest_keep_corpus(ctx, tmp_path)

    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "potluck", "mcp"],
        # StdioTransport env REPLACES the subprocess environment; pass the
        # full test environment so the isolated POTLUCK_* vars (autouse
        # isolated_dirs fixture) point the server at this test's database.
        env=dict(os.environ),
    )
    async with Client(transport) as client:
        names = {tool.name for tool in await client.list_tools()}
        assert names == EXPECTED_TOOLS

        result = await client.call_tool("search", {"query": "amber", "limit": 3})
        payload = result.structured_content
        assert payload is not None
        assert payload["hits"]
        hit = payload["hits"][0]
        assert "[amber]" in hit["snippet"].lower()  # bracketed [match] markers survive
