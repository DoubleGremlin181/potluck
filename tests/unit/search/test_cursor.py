"""Keyset cursor pagination (#128): no OFFSET, stable under concurrent inserts.

Cursors are BOUND to the effective query/prefix/filters that produced them —
replaying one under a different request raises InvalidCursorError instead of
silently skipping results.
"""

import base64
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from potluck.core.errors import InvalidCursorError
from potluck.models.items import ItemKind
from potluck.models.search import SearchRequest
from potluck.search.cursor import decode_cursor, encode_cursor
from potluck.services.context import AppContext
from potluck.services.search import search
from tests.conftest import insert_import, insert_item, insert_source

_BINDING = "00ff00ff00ff00ff"  # 16 hex chars, like the service's sha256 digest


def _b64(payload: str) -> str:
    return base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii")


# ---------------------------------------------------------------------------
# encode/decode
# ---------------------------------------------------------------------------


def test_cursor_round_trip_exact_float() -> None:
    score = -1.2345678901234567  # full double precision must survive
    cursor = encode_cursor(binding=_BINDING, max_id=982, last_score=score, last_id=41)
    decoded = decode_cursor(cursor)
    assert decoded.binding == _BINDING
    assert decoded.max_id == 982
    assert decoded.last_score == score  # exact, not repr-rounded
    assert decoded.last_id == 41


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "not base64 !!!",
        _b64("v2:abc"),  # wrong field count
        _b64(f"v1:{_BINDING}:1:400921fb54442d18:5"),  # unsupported version
        _b64(f"v2:{_BINDING}:x:400921fb54442d18:5"),  # non-int max_id
        _b64(f"v2:{_BINDING}:1:zzzz:5"),  # non-hex score
        _b64("v1:10:400921fb54442d18:5"),  # legacy pre-binding v1 layout
    ],
)
def test_garbage_cursor_raises(garbage: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(garbage)


def test_cursor_and_offset_mutually_exclusive() -> None:
    cursor = encode_cursor(binding=_BINDING, max_id=10, last_score=-1.0, last_id=3)
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


def test_cursor_walk_distinct_scores_no_skip_no_dup(ctx: AppContext) -> None:
    """Multi-page walk where page boundaries fall between DISTINCT scores,
    not only ties (the _seed docs all score identically): the keyset must
    resume strictly after (score, id) in both regimes — no skipped hits, no
    repeats, identical to offset ground truth."""

    def _go(conn: sqlite3.Connection) -> None:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        for n in range(13):
            # Vary term frequency and document length -> distinct bm25 scores.
            text = " ".join(["pear"] * (n % 4 + 1) + ["filler"] * n)
            insert_item(conn, sid, iid, content_hash=f"h{n}", title=f"doc {n}", text=text)

    ctx.db.write(_go)

    offset_ids = [
        h.id
        for off in (0, 5, 10)
        for h in search(ctx, SearchRequest(query="pear", limit=5, offset=off)).hits
    ]

    walked: list[tuple[int, float]] = []
    cursor: str | None = None
    for _ in range(10):  # safety bound
        resp = search(ctx, SearchRequest(query="pear", limit=5, cursor=cursor))
        walked.extend((h.id, h.score) for h in resp.hits)
        cursor = resp.next_cursor
        if cursor is None:
            break

    ids = [item_id for item_id, _ in walked]
    assert ids == offset_ids
    assert len(ids) == 13
    assert len(set(ids)) == 13, "cursor pages skipped or repeated a hit"
    # The seed must actually produce ranking variety, or this test degrades
    # into another equal-score walk.
    assert len({score for _, score in walked}) > 1


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


# ---------------------------------------------------------------------------
# query binding: a cursor is only valid for the request that produced it
# ---------------------------------------------------------------------------


def test_cursor_replayed_under_different_query_raises(ctx: AppContext) -> None:
    """A foreign cursor must raise, not silently skip everything scoring
    better than the foreign anchor."""
    _seed(ctx, 8)  # every item matches both "pear" and "tree"
    page1 = search(ctx, SearchRequest(query="pear", limit=5))
    assert page1.next_cursor is not None
    with pytest.raises(InvalidCursorError):
        search(ctx, SearchRequest(query="tree", limit=5, cursor=page1.next_cursor))


_CHANGED_FILTER_REQUESTS: list[Callable[[str], SearchRequest]] = [
    # structured fields
    lambda c: SearchRequest(query="pear", limit=5, cursor=c, kinds=[ItemKind.EMAIL]),
    lambda c: SearchRequest(query="pear", limit=5, cursor=c, sources=["other_src"]),
    lambda c: SearchRequest(query="pear", limit=5, cursor=c, from_addrs=["a@b.example"]),
    lambda c: SearchRequest(
        query="pear", limit=5, cursor=c, after=datetime(2020, 1, 1, tzinfo=UTC)
    ),
    lambda c: SearchRequest(
        query="pear", limit=5, cursor=c, before=datetime(2030, 1, 1, tzinfo=UTC)
    ),
    # the same filters expressed as inline operators
    lambda c: SearchRequest(query="pear kind:email", limit=5, cursor=c),
    lambda c: SearchRequest(query="pear source:other_src", limit=5, cursor=c),
    lambda c: SearchRequest(query="pear from:a@b.example", limit=5, cursor=c),
    lambda c: SearchRequest(query="pear after:2020-01-01", limit=5, cursor=c),
    lambda c: SearchRequest(query="pear before:2030-01-01", limit=5, cursor=c),
]


@pytest.mark.parametrize("make_req", _CHANGED_FILTER_REQUESTS)
def test_cursor_replayed_with_changed_filters_raises(
    ctx: AppContext, make_req: Callable[[str], SearchRequest]
) -> None:
    """Same terms, different effective filter set -> reject the cursor."""
    _seed(ctx, 8)
    page1 = search(ctx, SearchRequest(query="pear", limit=5))
    assert page1.next_cursor is not None
    with pytest.raises(InvalidCursorError):
        search(ctx, make_req(page1.next_cursor))


def test_cursor_replayed_with_prefix_flip_raises(ctx: AppContext) -> None:
    _seed(ctx, 8)
    exact = search(ctx, SearchRequest(query="pear", limit=5))
    assert exact.next_cursor is not None
    with pytest.raises(InvalidCursorError):
        search(ctx, SearchRequest(query="pear", limit=5, prefix=True, cursor=exact.next_cursor))

    sayt = search(ctx, SearchRequest(query="pear", limit=5, prefix=True))
    assert sayt.next_cursor is not None
    with pytest.raises(InvalidCursorError):
        search(ctx, SearchRequest(query="pear", limit=5, cursor=sayt.next_cursor))


def test_cursor_replayed_on_filter_only_query_raises(ctx: AppContext) -> None:
    """Cursors are only issued for ranked (free-text) searches, so a cursor
    arriving on a filter-only request is foreign by construction — reject it
    rather than silently restart offset paging from zero."""
    _seed(ctx, 8)
    page1 = search(ctx, SearchRequest(query="pear", limit=5))
    assert page1.next_cursor is not None
    with pytest.raises(InvalidCursorError):
        search(ctx, SearchRequest(query="kind:note", limit=5, cursor=page1.next_cursor))


def test_inline_and_structured_filters_bind_equally(ctx: AppContext) -> None:
    """The binding covers EFFECTIVE filters: `kind:note pear` and `pear` +
    kinds=["note"] are the same search, so their cursors interchange."""
    _seed(ctx, 8)
    page1 = search(ctx, SearchRequest(query="kind:note pear", limit=5))
    assert page1.next_cursor is not None

    page2 = search(
        ctx,
        SearchRequest(query="pear", kinds=[ItemKind.NOTE], limit=5, cursor=page1.next_cursor),
    )
    ids = [h.id for h in page1.hits] + [h.id for h in page2.hits]
    assert len(ids) == 8
    assert len(set(ids)) == 8, "pages overlapped despite equal effective filters"
    assert page2.next_cursor is None


def test_same_request_repeated_still_paginates(ctx: AppContext) -> None:
    """Binding must not break legitimate pagination: a full walk with the
    SAME query + filters + prefix mode sees every hit exactly once."""
    _seed(ctx, 12)
    collected: list[int] = []
    cursor: str | None = None
    for _ in range(10):  # safety bound
        resp = search(
            ctx, SearchRequest(query="pear kind:note", limit=5, prefix=True, cursor=cursor)
        )
        collected.extend(h.id for h in resp.hits)
        cursor = resp.next_cursor
        if cursor is None:
            break

    assert len(collected) == 12
    assert len(set(collected)) == 12
