"""Tests for potluck.services.items: get_item and list_items services."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from potluck.core.errors import ItemNotFoundError
from potluck.models.items import ItemKind, ItemSort, ListItemsRequest
from potluck.models.search import SearchRequest
from potluck.services.context import AppContext
from potluck.services.items import get_item, list_items
from potluck.services.search import search
from tests.conftest import ingest_keep_corpus, insert_import, insert_source

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_item_roundtrip(ctx: AppContext, tmp_path: Path) -> None:
    """get_item returns a fully-hydrated Item matching what was ingested."""
    ingest_keep_corpus(ctx, tmp_path)

    # Get an id via search (any known word from WORDS)
    req = SearchRequest(query="amber", limit=1)
    resp = search(ctx, req)
    assert resp.hits, "Need at least one search hit for roundtrip test"

    hit = resp.hits[0]
    item = get_item(ctx, hit.id)

    assert item.id == hit.id
    assert item.kind == ItemKind.NOTE  # Keep corpus = notes
    assert item.source == "google_keep"
    assert isinstance(item.meta, dict)

    # title and text may be None for list-style or empty notes, but for most notes:
    # At minimum the hit has a snippet, so something was indexed
    # If title was returned in hit, it matches
    if hit.title is not None:
        assert item.title == hit.title


def test_get_item_missing_raises(ctx: AppContext) -> None:
    """get_item raises ItemNotFoundError for a non-existent id."""
    with pytest.raises(ItemNotFoundError):
        get_item(ctx, 999999)


# ---------------------------------------------------------------------------
# list_items
# ---------------------------------------------------------------------------


def _seed_list_corpus(ctx: AppContext) -> None:
    """Two sources, mixed kinds, controlled timestamps (one NULL)."""

    def _setup(conn: sqlite3.Connection) -> None:
        src_keep = insert_source(conn, "google_keep")
        src_mail = insert_source(conn, "gmail")
        imp_keep = insert_import(conn, src_keep)
        imp_mail = insert_import(conn, src_mail)
        rows = [
            (src_keep, imp_keep, "note", "h1", "2024-01-01T00:00:00+00:00", "jan note"),
            (src_keep, imp_keep, "note", "h2", "2024-03-01T00:00:00+00:00", "mar note"),
            (src_keep, imp_keep, "note", "h3", None, "undated note"),
            (src_mail, imp_mail, "email", "h4", "2024-02-01T00:00:00+00:00", "feb email"),
            (src_mail, imp_mail, "email", "h5", "2024-04-01T00:00:00+00:00", "apr email"),
        ]
        conn.executemany(
            """INSERT INTO items (source_id, import_id, kind, content_hash, ts, title, text)
               VALUES (?, ?, ?, ?, ?, ?, 'body')""",
            rows,
        )

    ctx.db.write(_setup)


def test_list_items_default_newest_first_nulls_last(ctx: AppContext) -> None:
    _seed_list_corpus(ctx)
    resp = list_items(ctx, ListItemsRequest())

    assert resp.total == 5
    assert [i.title for i in resp.items] == [
        "apr email",
        "mar note",
        "feb email",
        "jan note",
        "undated note",  # NULL ts sorts last
    ]


def test_list_items_ts_asc_nulls_still_last(ctx: AppContext) -> None:
    _seed_list_corpus(ctx)
    resp = list_items(ctx, ListItemsRequest(sort=ItemSort.TS_ASC))

    assert [i.title for i in resp.items] == [
        "jan note",
        "feb email",
        "mar note",
        "apr email",
        "undated note",
    ]


def test_list_items_id_sorts(ctx: AppContext) -> None:
    _seed_list_corpus(ctx)
    asc = list_items(ctx, ListItemsRequest(sort=ItemSort.ID_ASC))
    desc = list_items(ctx, ListItemsRequest(sort=ItemSort.ID_DESC))

    assert [i.id for i in asc.items] == sorted(i.id for i in asc.items)
    assert [i.id for i in desc.items] == list(reversed([i.id for i in asc.items]))


def test_list_items_kind_filter(ctx: AppContext) -> None:
    _seed_list_corpus(ctx)
    resp = list_items(ctx, ListItemsRequest(kinds=[ItemKind.EMAIL]))

    assert resp.total == 2
    assert all(i.kind == ItemKind.EMAIL for i in resp.items)


def test_list_items_source_filter(ctx: AppContext) -> None:
    _seed_list_corpus(ctx)
    resp = list_items(ctx, ListItemsRequest(sources=["google_keep"]))

    assert resp.total == 3
    assert all(i.source == "google_keep" for i in resp.items)


def test_list_items_unknown_source_matches_nothing(ctx: AppContext) -> None:
    _seed_list_corpus(ctx)
    resp = list_items(ctx, ListItemsRequest(sources=["no-such-source"]))

    assert resp.total == 0
    assert resp.items == []


def test_list_items_since_inclusive_until_exclusive(ctx: AppContext) -> None:
    """since is >= and until is < — an item exactly at until is excluded; NULL ts
    rows never match a date filter."""
    _seed_list_corpus(ctx)
    resp = list_items(
        ctx,
        ListItemsRequest(
            since=datetime(2024, 2, 1, tzinfo=UTC),
            until=datetime(2024, 4, 1, tzinfo=UTC),
            sort=ItemSort.TS_ASC,
        ),
    )

    assert [i.title for i in resp.items] == ["feb email", "mar note"]
    assert resp.total == 2


def test_list_items_naive_datetimes_treated_as_utc(ctx: AppContext) -> None:
    _seed_list_corpus(ctx)
    req = ListItemsRequest(since=datetime(2024, 2, 1), until=datetime(2024, 4, 1))  # noqa: DTZ001
    resp = list_items(ctx, req)

    assert resp.total == 2


def test_list_items_pagination_deterministic(ctx: AppContext) -> None:
    _seed_list_corpus(ctx)
    page1 = list_items(ctx, ListItemsRequest(limit=2, offset=0))
    page2 = list_items(ctx, ListItemsRequest(limit=2, offset=2))
    page3 = list_items(ctx, ListItemsRequest(limit=2, offset=4))

    ids = [i.id for i in page1.items + page2.items + page3.items]
    assert len(ids) == 5
    assert len(set(ids)) == 5, "pages must be disjoint"
    # total is independent of pagination
    assert page1.total == page2.total == page3.total == 5
    assert (page1.limit, page1.offset) == (2, 0)


def test_list_items_text_preview_truncated(ctx: AppContext) -> None:
    def _setup(conn: sqlite3.Connection) -> None:
        src = insert_source(conn)
        imp = insert_import(conn, src)
        conn.execute(
            """INSERT INTO items (source_id, import_id, kind, content_hash, title, text)
               VALUES (?, ?, 'note', 'long', 'long note', ?)""",
            (src, imp, "x" * 500),
        )

    ctx.db.write(_setup)
    resp = list_items(ctx, ListItemsRequest())

    assert resp.items[0].text_preview == "x" * 200


def test_list_items_empty_db(ctx: AppContext) -> None:
    resp = list_items(ctx, ListItemsRequest())
    assert resp.items == []
    assert resp.total == 0
