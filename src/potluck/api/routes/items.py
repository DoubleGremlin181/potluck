"""REST items endpoints: offset-paged listing, item detail, thread view."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query

from potluck.api.deps import CtxDep
from potluck.api.errors import error_responses
from potluck.models.items import Item, ItemKind, ItemSort, ListItemsRequest, ListItemsResponse
from potluck.models.threads import ThreadResponse
from potluck.services import items as items_service
from potluck.services import threads as threads_service

router = APIRouter(tags=["items"])

_ITEM_ID = Annotated[int, Path(description="Item id, as returned by search hits and listing rows.")]


@router.get("/items", summary="List items", responses=error_responses(422))
def list_items(
    ctx: CtxDep,
    kind: Annotated[
        list[ItemKind] | None,
        Query(max_length=16, description="Only these item kinds (repeatable)."),
    ] = None,
    source: Annotated[
        list[str] | None,
        Query(
            max_length=64,
            description="Only these source names (repeatable); unknown names match nothing.",
        ),
    ] = None,
    since: Annotated[
        datetime | None,
        Query(description="Only items with ts on/after this (inclusive; naive means UTC)."),
    ] = None,
    until: Annotated[
        datetime | None,
        Query(description="Only items with ts before this (exclusive; naive means UTC)."),
    ] = None,
    sort: Annotated[
        ItemSort, Query(description="Sort order; undated items always sort last on ts.")
    ] = ItemSort.TS_DESC,
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum rows per page.")] = 20,
    offset: Annotated[int, Query(ge=0, description="Rows to skip before the page.")] = 0,
) -> ListItemsResponse:
    """Browse items without a keyword query: filters, sorting, paging.

    **Offset pagination contract**: page with `limit`/`offset`; `total` is
    the unpaginated count under the same filters, so pages exist while
    `offset < total`. Unlike `/api/search`, no cursor is involved and the
    window is not frozen — items ingested between requests shift pages.
    """
    return items_service.list_items(
        ctx,
        ListItemsRequest(
            kinds=kind,
            sources=source,
            since=since,
            until=until,
            sort=sort,
            limit=limit,
            offset=offset,
        ),
    )


@router.get("/items/{item_id}", summary="Fetch one item", responses=error_responses(404, 422))
def get_item(ctx: CtxDep, item_id: _ITEM_ID) -> Item:
    """Full content and metadata for one item.

    Kinds with satellite detail carry their detail block (`email` for
    emails: addresses, labels, attachments); other kinds leave it null.
    """
    return items_service.get_item(ctx, item_id)


@router.get(
    "/items/{item_id}/thread",
    summary="Fetch the conversation containing an item",
    responses=error_responses(404, 422),
)
def get_thread(ctx: CtxDep, item_id: _ITEM_ID) -> ThreadResponse:
    """Every message in the email thread containing this item, oldest first,
    with `parent_id` links forming the reply tree.

    For an item that is not part of any email thread, `entries` holds just
    the item itself and `thread_key` is null.
    """
    return threads_service.get_thread(ctx, item_id)
