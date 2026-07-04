"""MCP search + get_item tools: TDD tests verifying parity with the service layer."""

from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from potluck.mcp.server import create_mcp
from potluck.models.items import ItemKind, ListItemsRequest
from potluck.models.search import SearchRequest
from potluck.services import items as items_service
from potluck.services import search as search_service
from potluck.services.context import AppContext
from tests.conftest import ingest_keep_corpus

# ---------------------------------------------------------------------------
# Tool inventory
# ---------------------------------------------------------------------------


async def test_mcp_tool_inventory(ctx: AppContext) -> None:
    """Server exposes exactly the expected toolset."""
    async with Client(create_mcp(ctx)) as client:
        tools = {tool.name for tool in await client.list_tools()}
    assert tools == {"get_stats", "search", "list_items", "get_item", "get_thread", "list_sources"}


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------


async def test_search_tool_schema(ctx: AppContext) -> None:
    """search inputSchema includes query (required), kinds and limit params."""
    async with Client(create_mcp(ctx)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    search_tool = tools["search"]
    schema = search_tool.inputSchema
    props = schema.get("properties", {})

    assert "query" in props
    assert "kinds" in props
    assert "limit" in props
    # query must be required
    assert "query" in schema.get("required", [])


# ---------------------------------------------------------------------------
# search tool behaviour
# ---------------------------------------------------------------------------


async def test_mcp_search_returns_hits(ctx: AppContext, tmp_path: Path) -> None:
    """MCP search returns the same hits as calling the service layer directly."""
    ingest_keep_corpus(ctx, tmp_path)

    query = "amber"
    async with Client(create_mcp(ctx)) as client:
        result = await client.call_tool("search", {"query": query})

    assert not result.is_error
    structured = result.structured_content
    assert structured is not None

    # Hits must be non-empty for the known word
    hits = structured.get("hits", [])
    assert len(hits) > 0

    # Result must be identical to the service layer response
    expected = search_service.search(ctx, SearchRequest(query=query))
    assert structured == expected.model_dump(mode="json")


async def test_mcp_search_kind_filter(ctx: AppContext, tmp_path: Path) -> None:
    """Filtering by kind=email returns no hits (corpus is notes only)."""
    ingest_keep_corpus(ctx, tmp_path)

    async with Client(create_mcp(ctx)) as client:
        result = await client.call_tool("search", {"query": "amber", "kinds": [ItemKind.EMAIL]})

    assert not result.is_error
    structured = result.structured_content
    assert structured is not None
    assert structured.get("hits", []) == []


async def test_mcp_search_empty_query_ok(ctx: AppContext, tmp_path: Path) -> None:
    """Empty query string is handled gracefully — returns empty hits, no error."""
    ingest_keep_corpus(ctx, tmp_path)

    async with Client(create_mcp(ctx)) as client:
        result = await client.call_tool("search", {"query": ""})

    assert not result.is_error
    structured = result.structured_content
    assert structured is not None
    assert structured.get("hits", []) == []


# ---------------------------------------------------------------------------
# list_items tool behaviour
# ---------------------------------------------------------------------------


async def test_mcp_list_items_matches_service(ctx: AppContext, tmp_path: Path) -> None:
    """MCP list_items returns the same rows as the service layer."""
    ingest_keep_corpus(ctx, tmp_path)

    async with Client(create_mcp(ctx)) as client:
        result = await client.call_tool("list_items", {"limit": 5})

    assert not result.is_error
    structured = result.structured_content
    assert structured is not None
    assert len(structured["items"]) == 5

    expected = items_service.list_items(ctx, ListItemsRequest(limit=5))
    assert structured == expected.model_dump(mode="json")


async def test_mcp_list_items_kind_filter(ctx: AppContext, tmp_path: Path) -> None:
    """Filtering by kind=email returns nothing (corpus is notes only)."""
    ingest_keep_corpus(ctx, tmp_path)

    async with Client(create_mcp(ctx)) as client:
        result = await client.call_tool("list_items", {"kinds": [ItemKind.EMAIL]})

    assert not result.is_error
    structured = result.structured_content
    assert structured is not None
    assert structured["items"] == []
    assert structured["total"] == 0


# ---------------------------------------------------------------------------
# get_item tool behaviour
# ---------------------------------------------------------------------------


async def test_mcp_get_item_full_text(ctx: AppContext, tmp_path: Path) -> None:
    """get_item returns the full item; text and meta match the service layer exactly."""
    ingest_keep_corpus(ctx, tmp_path)

    # Get a known id from search
    search_resp = search_service.search(ctx, SearchRequest(query="amber"))
    assert search_resp.hits, "corpus must have hits for 'amber'"
    item_id = search_resp.hits[0].id

    async with Client(create_mcp(ctx)) as client:
        result = await client.call_tool("get_item", {"item_id": item_id})

    assert not result.is_error
    structured = result.structured_content
    assert structured is not None

    # Must match the service layer output exactly
    expected = items_service.get_item(ctx, item_id)
    assert structured == expected.model_dump(mode="json")

    # Full text and meta are present
    assert structured.get("text") is not None
    assert "meta" in structured


async def test_mcp_search_invalid_limit_is_tool_error(ctx: AppContext) -> None:
    """Out-of-range arguments surface as an informative ToolError (like
    get_item), not a masked internal error — and not pydantic's raw multiline
    report (#202)."""
    async with Client(create_mcp(ctx)) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("search", {"query": "x", "limit": 500})

    message = str(exc_info.value)
    assert "limit" in message.lower()
    assert "pydantic.dev" not in message
    assert "validation error for" not in message


async def test_mcp_search_oversized_kinds_list_is_tool_error(ctx: AppContext) -> None:
    """A kinds list beyond the DTO cap is a clean ToolError naming the field (#202)."""
    async with Client(create_mcp(ctx)) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("search", {"query": "x", "kinds": ["note"] * 17})

    message = str(exc_info.value)
    assert "kinds" in message
    assert "pydantic.dev" not in message


async def test_mcp_list_items_invalid_limit_is_tool_error(ctx: AppContext) -> None:
    async with Client(create_mcp(ctx)) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("list_items", {"limit": 500})

    message = str(exc_info.value)
    assert "limit" in message.lower()
    assert "pydantic.dev" not in message


async def test_mcp_get_item_missing_is_tool_error(ctx: AppContext) -> None:
    """Requesting a non-existent item surfaces as a ToolError with 'not found' in message."""
    async with Client(create_mcp(ctx)) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("get_item", {"item_id": 999999})

    assert "not found" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Warnings passthrough
# ---------------------------------------------------------------------------


async def test_mcp_search_warnings_passthrough(ctx: AppContext, tmp_path: Path) -> None:
    """Invalid inline operator values surface in warnings, matching the service."""
    ingest_keep_corpus(ctx, tmp_path)

    query = "kind:bogus amber"
    async with Client(create_mcp(ctx)) as client:
        result = await client.call_tool("search", {"query": query})

    assert not result.is_error
    structured = result.structured_content
    assert structured is not None
    expected = search_service.search(ctx, SearchRequest(query=query))
    assert structured["warnings"] == expected.warnings
    assert any("bogus" in w for w in structured["warnings"])


# ---------------------------------------------------------------------------
# Cursor pagination (#202)
# ---------------------------------------------------------------------------


async def test_mcp_search_cursor_round_trip(ctx: AppContext, tmp_path: Path) -> None:
    """A next_cursor passed back verbatim with the same query yields page two."""
    ingest_keep_corpus(ctx, tmp_path)

    async with Client(create_mcp(ctx)) as client:
        first = await client.call_tool("search", {"query": "amber", "limit": 2})
        page_one = first.structured_content
        assert page_one is not None
        assert page_one["next_cursor"] is not None

        second = await client.call_tool(
            "search", {"query": "amber", "limit": 2, "cursor": page_one["next_cursor"]}
        )

    page_two = second.structured_content
    assert page_two is not None
    first_ids = {hit["id"] for hit in page_one["hits"]}
    second_ids = {hit["id"] for hit in page_two["hits"]}
    assert second_ids and not (first_ids & second_ids)


async def test_mcp_search_foreign_cursor_is_tool_error(ctx: AppContext, tmp_path: Path) -> None:
    """A cursor replayed with a DIFFERENT query is a clean, actionable
    ToolError (#202) — not a protocol error or a raw traceback message."""
    ingest_keep_corpus(ctx, tmp_path)

    async with Client(create_mcp(ctx)) as client:
        first = await client.call_tool("search", {"query": "amber", "limit": 2})
        page_one = first.structured_content
        assert page_one is not None and page_one["next_cursor"] is not None

        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("search", {"query": "basil", "cursor": page_one["next_cursor"]})

    message = str(exc_info.value)
    assert "cursor" in message.lower()
    # Actionable recovery guidance for the calling model:
    assert "omit the cursor" in message


async def test_mcp_search_malformed_cursor_is_tool_error(ctx: AppContext) -> None:
    """Garbage cursors are rejected the same way as foreign ones (#202)."""
    async with Client(create_mcp(ctx)) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("search", {"query": "x", "cursor": "not-a-cursor"})

    message = str(exc_info.value)
    assert "cursor" in message.lower()
    assert "omit the cursor" in message


# ---------------------------------------------------------------------------
# Description tuning (#138 acceptance criterion)
# ---------------------------------------------------------------------------


async def test_search_description_teaches_query_language(ctx: AppContext) -> None:
    """The search description must teach an LLM the inline operators, quoting,
    prefix mode, warnings semantics and cursor etiquette."""
    async with Client(create_mcp(ctx)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    description = tools["search"].description or ""
    for operator in ("from:", "source:", "kind:", "after:", "before:"):
        assert operator in description, f"missing operator {operator}"
    assert '"' in description  # quoted-value example
    assert "prefix" in description.lower()
    assert "warnings" in description.lower()
    assert "cursor" in description.lower()
    assert "same query" in description.lower()  # cursor etiquette


async def test_list_items_description_contrasts_with_search(ctx: AppContext) -> None:
    async with Client(create_mcp(ctx)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    description = tools["list_items"].description or ""
    assert "search" in description.lower()
    assert "get_item" in description
