"""MCP list_sources tool (#138): DTO parity with GET /api/sources."""

from fastapi.testclient import TestClient
from fastmcp import Client

from potluck.mcp.server import create_mcp
from potluck.services.context import AppContext


async def test_list_sources_matches_api_dto(ctx: AppContext, api_client: TestClient) -> None:
    """The MCP tool and /api/sources return the identical payload from one service."""
    async with Client(create_mcp(ctx)) as client:
        result = await client.call_tool("list_sources")

    assert not result.is_error
    # fastmcp wraps non-object results under "result"; the payload inside must
    # be byte-for-byte the REST response.
    assert result.structured_content == {"result": api_client.get("/api/sources").json()}


async def test_list_sources_includes_registered_plugins(ctx: AppContext) -> None:
    """Real registry: both shipped plugins appear, sorted, with their kinds."""
    async with Client(create_mcp(ctx)) as client:
        result = await client.call_tool("list_sources")

    structured = result.structured_content
    assert structured is not None
    sources = structured["result"]
    names = [s["name"] for s in sources]
    assert names == sorted(names)
    assert "gmail" in names
    assert "google_keep" in names
    for source in sources:
        assert source["kinds"], f"source {source['name']} reports no kinds"


async def test_list_sources_description_written_for_ai(ctx: AppContext) -> None:
    """Description says what the tool is (registry, not database content) and
    links the names to the search source: operator."""
    async with Client(create_mcp(ctx)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    description = tools["list_sources"].description or ""
    assert "source:" in description
    assert "get_stats" in description
