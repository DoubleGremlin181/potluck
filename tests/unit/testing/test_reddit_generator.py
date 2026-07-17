"""Deterministic synthetic Reddit GDPR export generator (#143)."""

from pathlib import Path
from typing import Literal

from potluck.ingest.plugins import ParseContext, detect_sources
from potluck.ingest.readers import open_archive
from potluck.ingest.sources.reddit import parse
from potluck.models.items import ItemKind
from potluck.testing.reddit import (
    expected_item_counts,
    expected_link_post_count,
    reddit_members,
    write_reddit_export,
)


def test_generator_is_deterministic(tmp_path: Path) -> None:
    a = write_reddit_export(tmp_path / "a", posts=15, comments=12, saved_posts=4, seed=7)
    b = write_reddit_export(tmp_path / "b", posts=15, comments=12, saved_posts=4, seed=7)
    assert a.read_bytes() == b.read_bytes()


def test_seed_changes_content_but_not_structure() -> None:
    m1 = reddit_members(posts=5, comments=5, saved_posts=2, saved_comments=2, seed=1)
    m2 = reddit_members(posts=5, comments=5, saved_posts=2, saved_comments=2, seed=2)
    assert m1.keys() == m2.keys()
    assert m1["posts.csv"] != m2["posts.csv"]


def test_member_set_mirrors_real_export_shape() -> None:
    members = reddit_members(posts=3, comments=3, saved_posts=1, saved_comments=1, seed=7)
    # in-scope members + detection anchor + out-of-scope decoy
    for required in (
        "posts.csv",
        "comments.csv",
        "saved_posts.csv",
        "saved_comments.csv",
        "subscribed_subreddits.csv",
        "post_votes.csv",
    ):
        assert required in members, required
    # BOM tolerance is part of the parser contract — baked into the fixture
    assert members["posts.csv"].startswith(b"\xef\xbb\xbf")
    assert not members["comments.csv"].startswith(b"\xef\xbb\xbf")


def test_generated_export_parses_to_closed_form_counts(tmp_path: Path) -> None:
    archive = write_reddit_export(
        tmp_path, posts=20, comments=15, saved_posts=6, saved_comments=3, seed=7
    )
    assert [p.name for p in detect_sources(open_archive(archive))] == ["reddit"]
    drafts = list(parse(open_archive(archive), ParseContext()))

    by_kind: dict[str, int] = {}
    for d in drafts:
        by_kind[d.kind.value] = by_kind.get(d.kind.value, 0) + 1
    assert by_kind == expected_item_counts(posts=20, comments=15, saved_posts=6, saved_comments=3)

    link_posts = [d for d in drafts if d.kind is ItemKind.POST and "url" in d.meta]
    assert len(link_posts) == expected_link_post_count(20)

    comments = [d for d in drafts if d.meta.get("type") == "comment" and d.kind is ItemKind.POST]
    assert len(comments) == 15

    eids = [d.external_id for d in drafts]
    assert len(set(eids)) == len(eids)  # generator never repeats an identity


def test_generated_quoting_edge_cases_survive_the_parser(tmp_path: Path) -> None:
    """The shapes the generator promises (multiline, quotes+commas, emoji)
    must actually appear in parsed draft text."""
    archive = write_reddit_export(tmp_path, posts=12, comments=12, seed=7)
    drafts = list(parse(open_archive(archive), ParseContext()))
    texts = [d.text for d in drafts if d.text]
    assert any("\n" in t for t in texts), "no multiline body survived"
    assert any('"' in t and "," in t for t in texts), "no quoted/comma body survived"
    assert any("🎉" in t for t in texts), "no emoji body survived"


def test_all_archive_formats(tmp_path: Path) -> None:
    fmts: tuple[Literal["zip", "tgz", "dir"], ...] = ("zip", "tgz", "dir")
    for fmt in fmts:
        dest = write_reddit_export(tmp_path / fmt, posts=4, comments=4, seed=7, fmt=fmt)
        drafts = list(parse(open_archive(dest), ParseContext()))
        assert len(drafts) == 8, fmt
