"""Stats DTOs."""

from pydantic import BaseModel


class StatsResponse(BaseModel):
    """Database overview returned by the stats service (CLI status, /api/stats, MCP get_stats)."""

    version: str
    schema_version: int
    db_path: str
    db_size_bytes: int
    items: int
    sources: int
    imports: int
