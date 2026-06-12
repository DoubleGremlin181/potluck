"""get_stats service: typed zero counts on an empty database."""

from potluck import __version__
from potluck.services.context import AppContext
from potluck.services.stats import get_stats


def test_get_stats_zero_counts_on_empty_db(ctx: AppContext) -> None:
    stats = get_stats(ctx)
    assert stats.version == __version__
    assert stats.schema_version == 5
    assert stats.items == 0
    assert stats.sources == 0
    assert stats.imports == 0
    assert stats.db_size_bytes > 0
    assert stats.db_path.endswith("potluck.db")
