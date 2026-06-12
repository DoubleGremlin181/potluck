"""FastMCP server: thin MCP adapter over the service layer.

Tool descriptions are written for AI consumption — they tell the model when
to reach for the tool, not how it is implemented.
"""

from datetime import datetime

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from potluck import __version__
from potluck.core.errors import ItemNotFoundError
from potluck.models.items import Item, ItemKind, ItemSort, ListItemsRequest, ListItemsResponse
from potluck.models.search import SearchRequest, SearchResponse
from potluck.models.stats import StatsResponse
from potluck.models.threads import ThreadResponse
from potluck.services import items as items_service
from potluck.services import search as search_service
from potluck.services import stats as stats_service
from potluck.services import threads as threads_service
from potluck.services.context import AppContext, create_context

_INSTRUCTIONS = (
    "Potluck is the user's private, local-first personal knowledge database. "
    "It will hold their notes, emails, chats, and media ingested from data "
    "exports (Google Takeout and others). Use its tools to ground answers in "
    "the user's own data. Everything runs locally; nothing leaves the machine."
)


def create_mcp(ctx: AppContext | None = None) -> FastMCP:
    """Build the MCP server over an AppContext (created from config if omitted)."""
    context = ctx if ctx is not None else create_context()
    server = FastMCP(name="potluck", instructions=_INSTRUCTIONS, version=__version__)

    @server.tool
    def get_stats() -> StatsResponse:
        """Get an overview of the user's personal knowledge database: how many
        items, sources, and imports it currently holds, plus database location,
        size, and schema/app versions. Call this to check whether Potluck has
        data yet, or when the user asks what's in their knowledge base."""
        return stats_service.get_stats(context)

    @server.tool
    def search(
        query: str,
        kinds: list[ItemKind] | None = None,
        limit: int = 20,
    ) -> SearchResponse:
        """Search the user's personal knowledge base by keywords.

        Contains the user's own data ingested from their personal exports
        (currently Google Keep notes and Gmail; more sources arrive in later
        phases). Multi-word queries match items containing ALL words (no phrase
        search). The query string accepts inline operators, combinable with
        free text: from:addr (or from:name prefix), source:name, kind:email,
        after:YYYY-MM-DD (inclusive), before:YYYY-MM-DD (exclusive). Returns
        ranked hits (best first) with bracketed [match] snippets; follow up
        with get_item using a hit's id to read the full content. Use this
        whenever the user asks about anything they may have written down,
        saved, noted, or received by email.
        """
        return search_service.search(context, SearchRequest(query=query, kinds=kinds, limit=limit))

    @server.tool
    def list_items(
        kinds: list[ItemKind] | None = None,
        sources: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        sort: ItemSort = ItemSort.TS_DESC,
        limit: int = 20,
        offset: int = 0,
    ) -> ListItemsResponse:
        """Browse the user's items without a keyword query.

        Complements search: use this when the user wants recent items, a date
        range (since inclusive, until exclusive; naive datetimes mean UTC), or
        an inventory per kind/source rather than a keyword match. Returns one
        page of summaries (sorted newest-first by default) plus the unpaginated
        total; follow up with get_item using an item's id to read full content.
        """
        return items_service.list_items(
            context,
            ListItemsRequest(
                kinds=kinds,
                sources=sources,
                since=since,
                until=until,
                sort=sort,
                limit=limit,
                offset=offset,
            ),
        )

    @server.tool
    def get_item(item_id: int) -> Item:
        """Fetch one item's full content and metadata by id.

        Item IDs come from search hits. Returns the complete stored item including full
        text (search snippets are truncated), source name, timestamps and metadata.
        """
        try:
            return items_service.get_item(context, item_id)
        except ItemNotFoundError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool
    def get_thread(item_id: int) -> ThreadResponse:
        """Fetch the full email conversation containing one item.

        Item IDs come from search or list_items hits. Returns every message in
        the thread oldest-first (subject, sender, timestamp, text preview) with
        parent_id links forming the reply tree; follow up with get_item for a
        message's full body. For an item that is not an email the response
        holds just that item. Use this when the user asks about an email
        exchange or what was said in a conversation.
        """
        try:
            return threads_service.get_thread(context, item_id)
        except ItemNotFoundError as exc:
            raise ToolError(str(exc)) from exc

    return server


def run_stdio(ctx: AppContext | None = None) -> None:
    """Serve MCP over stdio (the default for local MCP clients)."""
    create_mcp(ctx).run(transport="stdio", show_banner=False)


def run_http(ctx: AppContext | None = None, *, host: str = "127.0.0.1", port: int = 8766) -> None:
    """Serve MCP over streamable HTTP."""
    create_mcp(ctx).run(transport="http", host=host, port=port, show_banner=False)
