"""Stats DTOs."""

from pydantic import BaseModel, Field

from potluck.models.items import ItemKind


class StatsResponse(BaseModel):
    """Database overview returned by the stats service (CLI status, /api/stats, MCP get_stats)."""

    version: str
    schema_version: int
    db_path: str
    db_size_bytes: int
    items: int
    items_by_kind: dict[ItemKind, int] = Field(
        description="Item counts per kind; kinds with zero items are omitted. "
        "Ordered by count descending, then kind name."
    )
    sources: int
    imports: int
