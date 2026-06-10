"""Items service: single-item retrieval."""

from potluck.core.errors import ItemNotFoundError
from potluck.models.items import Item
from potluck.services.context import AppContext
from potluck.storage.items import get_item_row, row_to_item


def get_item(ctx: AppContext, item_id: int) -> Item:
    """Fetch a single item by id.

    Args:
        ctx:     Application context carrying the open database.
        item_id: Primary key of the item to retrieve.

    Returns:
        A fully-hydrated :class:`~potluck.models.items.Item` DTO.

    Raises:
        ItemNotFoundError: If no item with *item_id* exists.
    """
    with ctx.db.read() as conn:
        result = get_item_row(conn, item_id)

    if result is None:
        raise ItemNotFoundError(f"item {item_id} not found")

    row, source_name = result
    return row_to_item(row, source_name)
