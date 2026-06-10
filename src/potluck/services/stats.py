"""Stats service: database overview for all three interfaces.

Services are plain sync functions ``(ctx, req) -> resp`` returning Pydantic
DTOs — this module sets the pattern.
"""

import sqlite3

from potluck import __version__
from potluck.models.stats import StatsResponse
from potluck.services.context import AppContext

# Tables that arrive in later phases; counted as 0 until they exist.
_COUNTED_TABLES = ("items", "sources", "imports")


def _count_if_exists(conn: sqlite3.Connection, table: str) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if exists is None:
        return 0
    return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def get_stats(ctx: AppContext) -> StatsResponse:
    """Counts and database facts; zero counts on an empty database."""
    with ctx.db.read() as conn:
        schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        counts = {table: _count_if_exists(conn, table) for table in _COUNTED_TABLES}
    return StatsResponse(
        version=__version__,
        schema_version=schema_version,
        db_path=str(ctx.settings.db_path),
        db_size_bytes=ctx.settings.db_path.stat().st_size,
        items=counts["items"],
        sources=counts["sources"],
        imports=counts["imports"],
    )
