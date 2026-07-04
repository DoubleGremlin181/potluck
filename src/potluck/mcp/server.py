"""FastMCP server: thin MCP adapter over the service layer.

Tool descriptions are written for AI consumption — they tell the model when
to reach for the tool and how to speak the query language, not how it is
implemented. Transports: stdio via :func:`run_stdio`; streamable HTTP is the
``/mcp`` mount inside the main FastAPI app (see ``api/app.py``), not a
separate server.
"""

from datetime import datetime

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from potluck import __version__
from potluck.core.errors import InvalidCursorError, ItemNotFoundError
from potluck.models.imports import SourceInfo
from potluck.models.items import Item, ItemKind, ItemSort, ListItemsRequest, ListItemsResponse
from potluck.models.search import SearchRequest, SearchResponse
from potluck.models.stats import StatsResponse
from potluck.models.threads import ThreadResponse
from potluck.services import imports as imports_service
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

_CURSOR_HINT = (
    "A cursor is only valid when passed back verbatim with the exact same "
    "query, prefix and filters that produced it; omit the cursor to start "
    "from the first page."
)


def _validation_summary(exc: ValidationError) -> str:
    """One clean ``param: problem`` line per offence — pydantic's multiline
    report (with its docs URL) is noise for a model-facing error message."""
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc']) or 'arguments'}: {err['msg']}"
        for err in exc.errors(include_url=False)
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
        prefix: bool = False,
        cursor: str | None = None,
        limit: int = 20,
    ) -> SearchResponse:
        """Search the user's personal knowledge base by keywords.

        It holds the user's own data from their personal exports (Google Keep
        notes and Gmail email today; more sources later) — use this whenever
        the user asks about anything they may have written down, saved,
        noted, or received. Multi-word queries match items containing ALL
        words (no phrase search).

        Inline operators combine with free text in the query string:
        from:addr (or a name prefix), source:gmail, kind:email,
        after:YYYY-MM-DD (inclusive), before:YYYY-MM-DD (exclusive). Quote
        values containing spaces: source:"google keep". Operators alone (no
        free text) list matching items newest-first. Invalid operator values
        are ignored, never errors — check the response's warnings and fix the
        query rather than trusting an unfiltered result.

        Returns ranked hits (best first) with bracketed [match] snippets;
        follow up with get_item on a hit's id for full content, or get_thread
        for the whole email conversation. Set prefix=true only for
        search-as-you-type: the last word then matches as a prefix. When
        next_cursor is non-null, fetch the next page by passing it back
        verbatim as cursor with the same query and parameters — never reuse a
        cursor after changing anything. To browse by date/kind/source without
        keywords, prefer list_items.
        """
        try:
            req = SearchRequest(query=query, kinds=kinds, prefix=prefix, cursor=cursor, limit=limit)
        except ValidationError as exc:
            raise ToolError(f"invalid search arguments — {_validation_summary(exc)}") from exc
        try:
            return search_service.search(context, req)
        except InvalidCursorError as exc:
            raise ToolError(f"invalid cursor — {exc}. {_CURSOR_HINT}") from exc

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
        total; page with offset. Follow up with get_item using an item's id to
        read full content.
        """
        try:
            req = ListItemsRequest(
                kinds=kinds,
                sources=sources,
                since=since,
                until=until,
                sort=sort,
                limit=limit,
                offset=offset,
            )
        except ValidationError as exc:
            raise ToolError(f"invalid list_items arguments — {_validation_summary(exc)}") from exc
        return items_service.list_items(context, req)

    @server.tool
    def get_item(item_id: int) -> Item:
        """Fetch one item's full content and metadata by id.

        Item IDs come from search, list_items, or get_thread results. Returns
        the complete stored item including full text (search snippets are
        truncated), source name, timestamps and metadata.
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

    @server.tool
    def list_sources() -> list[SourceInfo]:
        """List the data-source plugins this Potluck build can ingest: each
        entry is a source name plus the item kinds it produces. This is what
        COULD be imported, not what the database holds — call get_stats for
        actual content counts. The names are the valid values for search's
        source: operator and list_items' sources filter."""
        return imports_service.list_sources(context)

    return server


def run_stdio(ctx: AppContext | None = None) -> None:
    """Serve MCP over stdio (the default for local MCP clients)."""
    create_mcp(ctx).run(transport="stdio", show_banner=False)
