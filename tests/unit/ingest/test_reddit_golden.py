"""Golden test (#143): the committed Reddit fixture yields exact results.

The fixture is generator output only (see tests/fixtures/README.md); the
regeneration one-liner lives in potluck/testing/reddit.py. 24 posts, 30
comments, 6 saved posts, 5 saved comments, plus the detection anchor and
out-of-scope decoy members the parser must skip.
"""

from pathlib import Path

from potluck.services.context import AppContext
from potluck.services.imports import import_path
from potluck.testing.reddit import expected_link_post_count

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "reddit" / "reddit-synth-001"

GOLDEN_POSTS = 24
GOLDEN_COMMENTS = 30
GOLDEN_BOOKMARKS = 6 + 5
GOLDEN_POST_ITEMS = GOLDEN_POSTS + GOLDEN_COMMENTS
GOLDEN_COUNT = GOLDEN_POST_ITEMS + GOLDEN_BOOKMARKS

# Identity stability anchors: ids come straight from the export's id column,
# so these can only change if the identity policy itself changes — bump
# parser_version and say so in the commit if they do.
GOLDEN_FIRST_POST_EID = "reddit:t3_p000"
GOLDEN_FIRST_SAVED_EID = "reddit:saved:t3_sp000"


def test_fixture_exists() -> None:
    for member in ("posts.csv", "comments.csv", "saved_posts.csv", "saved_comments.csv"):
        assert (FIXTURE / member).is_file(), member


def test_golden_import_counts(ctx: AppContext) -> None:
    [run] = import_path(ctx, FIXTURE)
    assert run.source == "reddit"
    assert run.status == "completed"
    assert run.items_new == GOLDEN_COUNT
    assert run.items_duplicate == 0

    with ctx.db.read() as conn:
        posts = conn.execute("SELECT COUNT(*) FROM items WHERE kind = 'post'").fetchone()[0]
        bookmarks = conn.execute("SELECT COUNT(*) FROM items WHERE kind = 'bookmark'").fetchone()[0]
        comments = conn.execute(
            "SELECT COUNT(*) FROM items WHERE kind = 'post' "
            "AND json_extract(meta, '$.type') = 'comment'"
        ).fetchone()[0]
        link_posts = conn.execute(
            "SELECT COUNT(*) FROM items WHERE kind = 'post' "
            "AND json_extract(meta, '$.url') IS NOT NULL"
        ).fetchone()[0]
    assert posts == GOLDEN_POST_ITEMS
    assert bookmarks == GOLDEN_BOOKMARKS
    assert comments == GOLDEN_COMMENTS
    assert link_posts == expected_link_post_count(GOLDEN_POSTS)


def test_golden_identities_stable(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        eids = {str(r[0]) for r in conn.execute("SELECT external_id FROM items").fetchall()}
    assert GOLDEN_FIRST_POST_EID in eids
    assert GOLDEN_FIRST_SAVED_EID in eids
    assert all(eid.startswith("reddit:") for eid in eids)


def test_golden_shapes(ctx: AppContext) -> None:
    """Structural facts the fixture pins: posts have titles + timestamps,
    comments have neither title nor a missing body, bookmarks are undated but
    carry slug-derived titles and permalink meta."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        untitled_posts = conn.execute(
            "SELECT COUNT(*) FROM items WHERE kind = 'post' "
            "AND json_extract(meta, '$.type') = 'post' AND title IS NULL"
        ).fetchone()[0]
        titled_comments = conn.execute(
            "SELECT COUNT(*) FROM items WHERE kind = 'post' "
            "AND json_extract(meta, '$.type') = 'comment' AND title IS NOT NULL"
        ).fetchone()[0]
        dated_bookmarks = conn.execute(
            "SELECT COUNT(*) FROM items WHERE kind = 'bookmark' AND ts IS NOT NULL"
        ).fetchone()[0]
        untitled_bookmarks = conn.execute(
            "SELECT COUNT(*) FROM items WHERE kind = 'bookmark' AND title IS NULL"
        ).fetchone()[0]
        bookmarks_without_permalink = conn.execute(
            "SELECT COUNT(*) FROM items WHERE kind = 'bookmark' "
            "AND json_extract(meta, '$.permalink') IS NULL"
        ).fetchone()[0]
    assert untitled_posts == 0
    assert titled_comments == 0
    assert dated_bookmarks == 0
    assert untitled_bookmarks == 0
    assert bookmarks_without_permalink == 0


def test_golden_reimport_is_noop(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    [run2] = import_path(ctx, FIXTURE)
    assert run2.items_new == 0
    assert run2.items_duplicate == GOLDEN_COUNT
    assert run2.items_updated == 0
