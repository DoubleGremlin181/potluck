"""Items service: single-item retrieval and filtered listing."""

from potluck.core.errors import ItemNotFoundError
from potluck.models.items import (
    EmailDetail,
    Item,
    ItemKind,
    ItemSummary,
    ListItemsRequest,
    ListItemsResponse,
    LocationDetail,
    MessageDetail,
    TransactionDetail,
)
from potluck.services.context import AppContext
from potluck.storage.items import dt_to_iso, get_item_row, iso_to_dt, list_item_rows, row_to_item
from potluck.storage.satellites import SATELLITE_READERS


def get_item(ctx: AppContext, item_id: int) -> Item:
    """Fetch a single item by id, hydrating kind-specific satellite detail.

    Args:
        ctx:     Application context carrying the open database.
        item_id: Primary key of the item to retrieve.

    Returns:
        A fully-hydrated :class:`~potluck.models.items.Item` DTO. Kinds with
        a satellite reader (#200) carry their detail block (``email``,
        ``message``, ``transaction`` or ``location``); all other kinds leave
        them None.

    Raises:
        ItemNotFoundError: If no item with *item_id* exists.
    """
    with ctx.db.read() as conn:
        result = get_item_row(conn, item_id)
        if result is None:
            raise ItemNotFoundError(f"item {item_id} not found")
        row, source_name = result
        item = row_to_item(row, source_name)
        reader = SATELLITE_READERS.get(item.kind)
        if reader is not None:
            detail = reader(conn, item.id)
            if isinstance(detail, EmailDetail):
                item.email = detail
            elif isinstance(detail, MessageDetail):
                item.message = detail
            elif isinstance(detail, TransactionDetail):
                item.transaction = detail
            elif isinstance(detail, LocationDetail):
                item.location = detail
    return item


def list_items(ctx: AppContext, req: ListItemsRequest) -> ListItemsResponse:
    """Browse items without a search query: filters, sorting, pagination.

    Args:
        ctx: Application context carrying the open database.
        req: Filters (kinds, source names, ts range), sort order and page.
             Unknown source names simply match nothing.

    Returns:
        One page of :class:`~potluck.models.items.ItemSummary` rows plus the
        unpaginated total under the same filters.
    """
    with ctx.db.read() as conn:
        rows, total = list_item_rows(
            conn,
            kinds=req.kinds,
            sources=req.sources,
            since_iso=dt_to_iso(req.since) if req.since is not None else None,
            until_iso=dt_to_iso(req.until) if req.until is not None else None,
            sort=req.sort,
            limit=req.limit,
            offset=req.offset,
        )

    items = [
        ItemSummary(
            id=int(row["id"]),
            source=str(row["source_name"]),
            kind=ItemKind(row["kind"]),
            ts=iso_to_dt(row["ts"]) if row["ts"] is not None else None,
            title=row["title"],
            text_preview=row["text_preview"],
        )
        for row in rows
    ]
    return ListItemsResponse(items=items, total=total, limit=req.limit, offset=req.offset)
