"""Keyset cursor pagination (#128): no OFFSET, stable under concurrent inserts."""

import sqlite3

import pytest
from pydantic import ValidationError

from potluck.core.errors import InvalidCursorError
from potluck.models.search import SearchRequest
from potluck.search.cursor import decode_cursor, encode_cursor
from potluck.services.context import AppContext
from potluck.services.search import search
from tests.conftest import insert_import, insert_item, insert_source

# ---------------------------------------------------------------------------
# encode/decode
# ---------------------------------------------------------------------------


def test_cursor_round_trip_exact_float() -> None:
    score = -1.2345678901234567  # full double precision must survive
    cursor = encode_cursor(max_id=982, last_score=score, last_id=41)
    decoded = decode_cursor(cursor)
    assert decoded.max_id == 982
    assert decoded.last_score == score  # exact, not repr-rounded
    assert decoded.last_id == 41


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "not base64 !!!",
        "djE6YWJj",  # b64("v1:abc") — wrong field count
        "djI6MTo0MDA5MjFlOTo1",  # wrong version v2
        "djE6eDo0MDA5MjFlOTo1",  # non-int max_id
        "djE6MTp6enp6OjU=",  # non-hex score
    ],
)
def test_garbage_cursor_raises(garbage: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(garbage)


def test_cursor_and_offset_mutually_exclusive() -> None:
    cursor = encode_cursor(max_id=10, last_score=-1.0, last_id=3)
    with pytest.raises(ValidationError):
        SearchRequest(query="x", cursor=cursor, offset=5)


# ---------------------------------------------------------------------------
# service pagination
# ---------------------------------------------------------------------------


def _seed(ctx: AppContext, count: int, *, start: int = 0) -> None:
    def _go(conn: sqlite3.Connection) -> None:
        if start == 0:
            sid = insert_source(conn)
            iid = insert_import(conn, sid)
        else:
            sid, iid = 1, 1
        for n in range(start, start + count):
            insert_item(conn, sid, iid, content_hash=f"h{n}", title=f"pear {n}", text="pear tree")

    ctx.db.write(_go)


def test_cursor_pages_match_offset_ground_truth(ctx: AppContext) -> None:
    _seed(ctx, 25)
    offset_ids = [
        h.id
        for off in (0, 10, 20)
        for h in search(ctx, SearchRequest(query="pear", limit=10, offset=off)).hits
    ]

    cursor_ids: list[int] = []
    cursor: str | None = None
    for _ in range(10):  # safety bound
        req = SearchRequest(query="pear", limit=10, cursor=cursor)
        resp = search(ctx, req)
        cursor_ids.extend(h.id for h in resp.hits)
        cursor = resp.next_cursor
        if cursor is None:
            break

    assert cursor_ids == offset_ids
    assert len(cursor_ids) == 25


def test_next_cursor_none_when_exhausted(ctx: AppContext) -> None:
    _seed(ctx, 5)
    resp = search(ctx, SearchRequest(query="pear", limit=10))
    assert len(resp.hits) == 5
    assert resp.next_cursor is None


def test_cursor_stable_under_concurrent_inserts(ctx: AppContext) -> None:
    """Docs inserted between page fetches must not shift, repeat, or hide
    any pre-existing hit: the cursor freezes the candidate set at max_id."""
    _seed(ctx, 12)
    with ctx.db.read() as conn:
        original_ids = {int(r[0]) for r in conn.execute("SELECT id FROM items").fetchall()}

    page1 = search(ctx, SearchRequest(query="pear", limit=5))
    assert page1.next_cursor is not None

    _seed(ctx, 6, start=100)  # new matching docs land mid-pagination

    collected = [h.id for h in page1.hits]
    cursor: str | None = page1.next_cursor
    while cursor is not None:
        resp = search(ctx, SearchRequest(query="pear", limit=5, cursor=cursor))
        collected.extend(h.id for h in resp.hits)
        cursor = resp.next_cursor

    assert len(collected) == len(set(collected)), "cursor pages repeated a hit"
    assert set(collected) == original_ids, "cursor pages skipped or invented hits"


def test_filter_only_query_has_no_cursor(ctx: AppContext) -> None:
    """Keyset cursors require ranked terms; filter-only queries page by offset."""
    _seed(ctx, 5)
    resp = search(ctx, SearchRequest(query="kind:note", limit=3))
    assert resp.next_cursor is None


def test_invalid_cursor_surfaces_cleanly(ctx: AppContext) -> None:
    _seed(ctx, 3)
    with pytest.raises(InvalidCursorError):
        search(ctx, SearchRequest(query="pear", cursor="garbage!"))
