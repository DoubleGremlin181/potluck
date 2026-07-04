"""Streamable HTTP transport mounted at /mcp inside the main app (#138).

One server surface: the FastAPI app on the serve port carries the SPA, the
REST API and the MCP endpoint. The end-to-end test runs the real app under
uvicorn on an ephemeral port — the composed lifespan (imports sweep + fastmcp
session manager) must come up for either half to answer.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from potluck.api.app import create_app
from potluck.services.context import AppContext
from tests.conftest import ingest_keep_corpus


@asynccontextmanager
async def _serve(ctx: AppContext) -> AsyncIterator[str]:
    """Run the real FastAPI app under uvicorn on an ephemeral port."""
    config = uvicorn.Config(
        create_app(ctx), host="127.0.0.1", port=0, log_level="error", lifespan="on"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(15):
            while not server.started:
                await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=15)


async def test_mounted_mcp_endpoint_end_to_end(ctx: AppContext, tmp_path: Path) -> None:
    """A local MCP client pointed at <serve-url>/mcp lists tools and searches,
    while the REST API answers on the same port."""
    ingest_keep_corpus(ctx, tmp_path)

    async with _serve(ctx) as base_url:
        async with Client(StreamableHttpTransport(f"{base_url}/mcp")) as client:
            tools = {tool.name for tool in await client.list_tools()}
            assert "search" in tools
            assert "list_sources" in tools

            result = await client.call_tool("search", {"query": "amber"})
            structured = result.structured_content
            assert structured is not None
            assert structured["hits"]

        # Same port, same app: the REST half still serves.
        async with httpx.AsyncClient() as http:
            resp = await http.get(f"{base_url}/api/stats")
            assert resp.status_code == 200


def test_mcp_mount_exists_in_app(api_client: TestClient) -> None:
    """The /mcp mount is part of the FastAPI app (cheap no-uvicorn check)."""
    resp = api_client.post("/mcp/", json={})
    assert resp.status_code != 404
