"""Search-as-you-type prefix mode (#128)."""

from potluck.models.search import SearchRequest
from potluck.search.fts import sanitize_query
from potluck.services.context import AppContext
from potluck.services.search import search
from tests.conftest import insert_import, insert_item, insert_source


def test_sanitize_prefix_last_token_starred() -> None:
    assert sanitize_query("gar", prefix=True) == '"gar"*'
    assert sanitize_query("garden pl", prefix=True) == '"garden" "pl"*'


def test_sanitize_prefix_single_char_and_empty() -> None:
    assert sanitize_query("g", prefix=True) == '"g"*'
    assert sanitize_query("", prefix=True) is None
    assert sanitize_query("***", prefix=True) is None


def test_sanitize_default_stays_exact() -> None:
    assert sanitize_query("garden pl") == '"garden" "pl"'


def _seed_notes(ctx: AppContext, titles: list[str]) -> None:
    def _go(conn: object) -> None:
        import sqlite3

        assert isinstance(conn, sqlite3.Connection)
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        for n, title in enumerate(titles):
            insert_item(conn, sid, iid, content_hash=f"h{n}", title=title, text=title)

    ctx.db.write(_go)


def test_prefix_search_matches_word_starts(ctx: AppContext) -> None:
    _seed_notes(ctx, ["garden plans", "garlic harvest", "garnet ring", "maple syrup"])
    resp = search(ctx, SearchRequest(query="gar", prefix=True))
    assert len(resp.hits) == 3
    exact = search(ctx, SearchRequest(query="gar"))
    assert exact.hits == []


def test_prefix_only_applies_to_last_token(ctx: AppContext) -> None:
    _seed_notes(ctx, ["garden plans", "garden plates", "garlic plans"])
    resp = search(ctx, SearchRequest(query="garden pla", prefix=True))
    assert {h.title for h in resp.hits} == {"garden plans", "garden plates"}


def test_prefix_composes_with_operators(ctx: AppContext) -> None:
    _seed_notes(ctx, ["garden plans", "garlic harvest"])
    resp = search(ctx, SearchRequest(query="kind:note gar", prefix=True))
    assert len(resp.hits) == 2


def test_title_highlight_present(ctx: AppContext) -> None:
    _seed_notes(ctx, ["garden plans"])
    resp = search(ctx, SearchRequest(query="garden"))
    assert resp.hits[0].title_highlight == "[garden] plans"


def test_title_highlight_none_for_filter_only(ctx: AppContext) -> None:
    _seed_notes(ctx, ["garden plans"])
    resp = search(ctx, SearchRequest(query="kind:note"))
    assert resp.hits[0].title_highlight is None
