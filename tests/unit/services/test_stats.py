"""get_stats service: typed zero counts on an empty database, per-kind counts."""

from pathlib import Path

from potluck import __version__
from potluck.models.items import ItemKind
from potluck.services.context import AppContext
from potluck.services.stats import get_stats
from tests.conftest import email_draft, ingest_email_drafts, ingest_keep_corpus


def test_get_stats_zero_counts_on_empty_db(ctx: AppContext) -> None:
    stats = get_stats(ctx)
    assert stats.version == __version__
    assert stats.schema_version == 10
    assert stats.items == 0
    assert stats.items_by_kind == {}
    assert stats.sources == 0
    assert stats.imports == 0
    assert stats.db_size_bytes > 0
    assert stats.db_path.endswith("potluck.db")


def test_get_stats_counts_items_by_kind(ctx: AppContext, tmp_path: Path) -> None:
    ingest_keep_corpus(ctx, tmp_path, count=5)
    ingest_email_drafts(ctx, email_draft(1), email_draft(2), email_draft(3))

    stats = get_stats(ctx)
    assert stats.items == 8
    assert stats.items_by_kind == {ItemKind.NOTE: 5, ItemKind.EMAIL: 3}
    # Largest kind first; zero kinds are omitted, so items is the sum.
    assert list(stats.items_by_kind) == [ItemKind.NOTE, ItemKind.EMAIL]
    assert sum(stats.items_by_kind.values()) == stats.items
    assert stats.sources == 2
    assert stats.imports == 2
