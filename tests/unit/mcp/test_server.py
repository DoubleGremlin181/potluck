"""MCP shell: get_stats tool parity with the API, transport wiring."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP

from potluck.mcp.server import create_mcp, run_http, run_stdio
from potluck.services.context import AppContext


async def test_get_stats_tool_matches_api_dto(ctx: AppContext, api_client: TestClient) -> None:
    """The MCP tool and /api/stats return the identical payload from one service."""
    async with Client(create_mcp(ctx)) as client:
        result = await client.call_tool("get_stats")
    assert result.structured_content == api_client.get("/api/stats").json()


async def test_get_stats_description_written_for_ai(ctx: AppContext) -> None:
    async with Client(create_mcp(ctx)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
    assert "get_stats" in tools
    description = tools["get_stats"].description or ""
    assert "knowledge database" in description


def test_run_helpers_wire_transports(ctx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(self: FastMCP, transport: str | None = None, **kwargs: Any) -> None:
        calls.append({"transport": transport, **kwargs})

    monkeypatch.setattr(FastMCP, "run", fake_run)
    run_stdio(ctx)
    run_http(ctx, host="127.0.0.1", port=8766)
    assert calls[0]["transport"] == "stdio"
    assert calls[1]["transport"] == "http"
    assert calls[1]["host"] == "127.0.0.1"
    assert calls[1]["port"] == 8766
