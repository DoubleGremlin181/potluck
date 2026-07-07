"""MCP shell: get_stats tool parity with the API, transport wiring."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP

import potluck.mcp.server
from potluck.mcp.server import create_mcp, run_stdio
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


def test_run_stdio_wires_transport(ctx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(self: FastMCP, transport: str | None = None, **kwargs: Any) -> None:
        calls.append({"transport": transport, **kwargs})

    monkeypatch.setattr(FastMCP, "run", fake_run)
    run_stdio(ctx)
    assert calls == [{"transport": "stdio", "show_banner": False}]


def test_separate_http_port_path_removed() -> None:
    """The pre-#138 separate-port HTTP server (8766) is gone: streamable HTTP
    is served only as the /mcp mount inside the main app."""
    assert not hasattr(potluck.mcp.server, "run_http")
