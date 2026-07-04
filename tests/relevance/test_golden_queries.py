"""Run every golden query against the session corpus (#129).

On failure the full ranked result list is printed (id, external_id, score,
title, snippet) so ranking regressions are debuggable from the CI log alone.
"""

import pytest

from potluck.models.search import SearchRequest
from potluck.services.context import AppContext
from potluck.services.search import search
from tests.relevance.golden_queries import GOLDEN, GoldenQuery


def _ranked_dump(ctx: AppContext, golden: GoldenQuery) -> str:
    resp = search(
        ctx,
        SearchRequest(query=golden.query, prefix=golden.prefix, limit=min(golden.k * 2, 100)),
    )
    with ctx.db.read() as conn:
        eids = {
            int(row[0]): row[1]
            for row in conn.execute(
                f"SELECT id, external_id FROM items WHERE id IN ({','.join('?' * len(resp.hits))})",
                [h.id for h in resp.hits],
            ).fetchall()
        }
    lines = [f"query={golden.query!r} prefix={golden.prefix} k={golden.k} — ranked results:"]
    for rank, hit in enumerate(resp.hits, start=1):
        lines.append(
            f"  {rank:2d}. id={hit.id} eid={eids.get(hit.id)!r} "
            f"score={hit.score:.4f} title={hit.title!r} snippet={hit.snippet!r}"
        )
    if not resp.hits:
        lines.append("  (no hits)")
    return "\n".join(lines)


@pytest.mark.parametrize("golden", GOLDEN, ids=lambda g: g.query)
def test_golden_query(relevance_ctx: AppContext, golden: GoldenQuery) -> None:
    resp = search(
        relevance_ctx,
        SearchRequest(query=golden.query, prefix=golden.prefix, limit=golden.k),
    )
    hit_ids = [hit.id for hit in resp.hits]
    hit_titles = {hit.title for hit in resp.hits}

    if golden.expect_titles and hit_titles & set(golden.expect_titles):
        return
    if golden.expect_external_ids:
        with relevance_ctx.db.read() as conn:
            placeholders = ",".join("?" * len(golden.expect_external_ids))
            expected_ids = {
                int(row[0])
                for row in conn.execute(
                    f"SELECT id FROM items WHERE external_id IN ({placeholders})",
                    list(golden.expect_external_ids),
                ).fetchall()
            }
        assert expected_ids, (
            f"none of {golden.expect_external_ids} exist in the corpus — "
            f"golden entry is stale, regenerate expectations"
        )
        if expected_ids & set(hit_ids):
            return

    pytest.fail(
        f"expected one of titles={golden.expect_titles} / "
        f"eids={golden.expect_external_ids} in top {golden.k}\n"
        + _ranked_dump(relevance_ctx, golden)
    )


def test_golden_suite_size() -> None:
    """The eval stays meaningful: ~30 queries minimum, extended every phase."""
    assert len(GOLDEN) >= 30, f"only {len(GOLDEN)} golden queries"
