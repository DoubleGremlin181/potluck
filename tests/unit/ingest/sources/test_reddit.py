"""Tests for the Reddit GDPR export source plugin.

Testing private helpers (_parse_posts, _parse_comments, _parse_saved_*) is
intentional: the CSV discipline (quoting, BOM, containment), the kind
mapping, and the identity policy are the public contract of this module and
must be covered at the unit level, from synthetic bytes.

Column headers here mirror the real 2025 GDPR export (shape only — all row
content is synthetic).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from potluck.ingest.plugins import ParseContext, detect_sources, discover
from potluck.ingest.readers import open_archive
from potluck.ingest.sources.reddit import (
    _parse_comments,
    _parse_posts,
    _parse_saved_comments,
    _parse_saved_posts,
    parse,
)
from potluck.models.drafts import BookmarkDraft, PostDraft
from potluck.models.items import ItemKind
from potluck.testing.archives import write_archive

_POSTS_HEADER = "id,permalink,date,ip,subreddit,gildings,title,url,body\n"
_COMMENTS_HEADER = "id,permalink,date,ip,subreddit,gildings,link,parent,body,media\n"
_SAVED_HEADER = "id,permalink\n"

_PERMALINK = "https://www.reddit.com/r/synthcooking/comments/abc12/my_first_stew/"


def _posts(csv_text: str, member: str = "posts.csv") -> list[PostDraft]:
    return list(_parse_posts(csv_text.encode(), member))


def _comments(csv_text: str, member: str = "comments.csv") -> list[PostDraft]:
    return list(_parse_comments(csv_text.encode(), member))


# ---------------------------------------------------------------------------
# posts.csv → kind=post
# ---------------------------------------------------------------------------


def test_post_basic_mapping() -> None:
    row = f'abc12,{_PERMALINK},2023-06-15 14:30:00 UTC,,synthcooking,0,My First Stew,{_PERMALINK},"Just simmered it."\n'
    [d] = _posts(_POSTS_HEADER + row)
    assert d.kind is ItemKind.POST
    assert d.external_id == "reddit:t3_abc12"
    assert d.ts == datetime(2023, 6, 15, 14, 30, tzinfo=UTC)
    assert d.title == "My First Stew"
    assert d.text == "Just simmered it."
    assert d.meta == {
        "type": "post",
        "subreddit": "synthcooking",
        "permalink": _PERMALINK,
    }


def test_link_post_keeps_external_url_in_meta() -> None:
    row = f"abc12,{_PERMALINK},2023-06-15 14:30:00 UTC,,synthcooking,0,A Link,https://example.com/article,\n"
    [d] = _posts(_POSTS_HEADER + row)
    assert d.text is None  # this link post has no body (empty body → None)
    assert d.meta["url"] == "https://example.com/article"


def test_link_post_with_body_keeps_both() -> None:
    """Modern reddit allows body text on link posts — both must survive."""
    row = f"abc12,{_PERMALINK},2023-06-15 14:30:00 UTC,,synthcooking,0,T,https://example.com/a,why I like it\n"
    [d] = _posts(_POSTS_HEADER + row)
    assert d.text == "why I like it"
    assert d.meta["url"] == "https://example.com/a"


def test_self_post_url_equal_to_permalink_is_not_duplicated() -> None:
    row = f"abc12,{_PERMALINK},2023-06-15 14:30:00 UTC,,synthcooking,0,T,{_PERMALINK},hello\n"
    [d] = _posts(_POSTS_HEADER + row)
    assert "url" not in d.meta


def test_old_style_relative_permalink_with_absolute_url_is_not_a_link_post() -> None:
    """Old exports pair a site-relative permalink with an absolute url for
    the SAME self post — the comparison must be by path, never by string."""
    relative = "/r/synthcooking/comments/abc12/my_first_stew/"
    absolute = f"https://reddit.com{relative}"
    row = f"abc12,{relative},2023-06-15 14:30:00 UTC,,synthcooking,0,T,{absolute},hello\n"
    [d] = _posts(_POSTS_HEADER + row)
    assert "url" not in d.meta


def test_crosspost_to_reddit_url_is_kept() -> None:
    """A reddit-hosted url with a DIFFERENT path is a real target (crosspost)."""
    other = "https://www.reddit.com/r/fixturegardens/comments/zzz99/other_thing/"
    row = f"abc12,{_PERMALINK},2023-06-15 14:30:00 UTC,,synthcooking,0,T,{other},\n"
    [d] = _posts(_POSTS_HEADER + row)
    assert d.meta["url"] == other


def test_quoted_multiline_body_with_commas_and_quotes_survives() -> None:
    body = 'first line, with a comma\nsecond "quoted" line\n\nfourth line'
    escaped = body.replace('"', '""')
    row = f'abc12,{_PERMALINK},2023-06-15 14:30:00 UTC,,synthcooking,0,T,{_PERMALINK},"{escaped}"\n'
    [d] = _posts(_POSTS_HEADER + row)
    assert d.text == body


def test_bom_is_tolerated() -> None:
    text = (
        "\ufeff"
        + _POSTS_HEADER
        + f"abc12,{_PERMALINK},2023-06-15 14:30:00 UTC,,synthcooking,0,T,{_PERMALINK},x\n"
    )
    [d] = _posts(text)
    assert d.external_id == "reddit:t3_abc12"  # BOM must not glue onto the first header


def test_crlf_rows_parse() -> None:
    text = _POSTS_HEADER.replace("\n", "\r\n") + (
        f"abc12,{_PERMALINK},2023-06-15 14:30:00 UTC,,synthcooking,0,T,{_PERMALINK},x\r\n"
    )
    [d] = _posts(text)
    assert d.text == "x"


def test_already_prefixed_id_is_not_double_prefixed() -> None:
    """Old-generation exports carry t3_/t1_ fullnames; modern ones bare ids."""
    row = f"t3_abc12,{_PERMALINK},2023-06-15 14:30:00 UTC,,synthcooking,0,T,{_PERMALINK},x\n"
    [d] = _posts(_POSTS_HEADER + row)
    assert d.external_id == "reddit:t3_abc12"


def test_row_without_id_is_skipped_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    text = _POSTS_HEADER + (
        f",{_PERMALINK},2023-06-15 14:30:00 UTC,,synthcooking,0,T,{_PERMALINK},x\n"
        f"ok1,{_PERMALINK},2023-06-15 14:31:00 UTC,,synthcooking,0,T,{_PERMALINK},y\n"
    )
    with caplog.at_level(logging.WARNING):
        drafts = _posts(text)
    assert [d.text for d in drafts] == ["y"]
    assert any("no id" in r.message for r in caplog.records)


def test_unparseable_date_keeps_row_without_ts_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The id is the identity — a bad date must not lose the content."""
    row = f"abc12,{_PERMALINK},yesterday-ish,,synthcooking,0,T,{_PERMALINK},x\n"
    with caplog.at_level(logging.WARNING):
        [d] = _posts(_POSTS_HEADER + row)
    assert d.ts is None
    assert d.text == "x"
    assert any("date" in r.message for r in caplog.records)


def test_date_without_utc_suffix_parses_as_utc() -> None:
    row = f"abc12,{_PERMALINK},2023-06-15 14:30:00,,synthcooking,0,T,{_PERMALINK},x\n"
    [d] = _posts(_POSTS_HEADER + row)
    assert d.ts == datetime(2023, 6, 15, 14, 30, tzinfo=UTC)


def test_empty_date_yields_no_ts_and_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    row = f"abc12,{_PERMALINK},,,synthcooking,0,T,{_PERMALINK},x\n"
    with caplog.at_level(logging.WARNING):
        [d] = _posts(_POSTS_HEADER + row)
    assert d.ts is None
    assert not caplog.records


def test_member_with_missing_required_columns_is_skipped_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A renamed/foreign header must never misparse silently."""
    with caplog.at_level(logging.WARNING):
        drafts = _posts("identifier,link\nabc12,https://example.com/x\n")
    assert drafts == []
    assert any("posts.csv" in r.message and "column" in r.message for r in caplog.records)


def test_header_only_member_yields_nothing_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No posts is a legitimate account state, not a parse failure."""
    with caplog.at_level(logging.WARNING):
        assert _posts(_POSTS_HEADER) == []
    assert not caplog.records


def test_emoji_and_rtl_text_survive() -> None:
    row = f'abc12,{_PERMALINK},2023-06-15 14:30:00 UTC,,synthcooking,0,عنوان,{_PERMALINK},"نص عربي שלום 🎉🚀"\n'
    [d] = _posts(_POSTS_HEADER + row)
    assert d.title == "عنوان"
    assert d.text == "نص عربي שלום 🎉🚀"


# ---------------------------------------------------------------------------
# comments.csv → kind=post, meta.type=comment
# ---------------------------------------------------------------------------


def test_comment_basic_mapping() -> None:
    link = "https://www.reddit.com/r/synthcooking/comments/xyz99/other_stew/"
    permalink = f"{link}c0mm1/"
    row = f'c0mm1,{permalink},2023-06-16 08:00:00 UTC,,synthcooking,0,{link},t3_xyz99,"Nice stew, chef!",\n'
    [d] = _comments(_COMMENTS_HEADER + row)
    assert d.kind is ItemKind.POST
    assert d.external_id == "reddit:t1_c0mm1"
    assert d.ts == datetime(2023, 6, 16, 8, 0, tzinfo=UTC)
    assert d.title is None
    assert d.text == "Nice stew, chef!"
    assert d.meta == {
        "type": "comment",
        "subreddit": "synthcooking",
        "permalink": permalink,
        "link": link,
        "parent": "t3_xyz99",
    }


def test_comment_empty_optional_columns_stay_out_of_meta() -> None:
    """parent is empty on 30% of real rows; media has never been observed
    non-empty — absent values must not appear as empty-string meta keys."""
    row = f"c0mm1,{_PERMALINK}c0mm1/,2023-06-16 08:00:00 UTC,,synthcooking,0,,,body text,\n"
    [d] = _comments(_COMMENTS_HEADER + row)
    assert "parent" not in d.meta
    assert "link" not in d.meta
    assert "media" not in d.meta


# ---------------------------------------------------------------------------
# saved_posts.csv / saved_comments.csv → kind=bookmark
# ---------------------------------------------------------------------------


def test_saved_post_maps_to_bookmark() -> None:
    text = _SAVED_HEADER + f"sav01,{_PERMALINK}\n"
    [d] = list(_parse_saved_posts(text.encode(), "saved_posts.csv"))
    assert isinstance(d, BookmarkDraft)
    assert d.kind is ItemKind.BOOKMARK
    assert d.external_id == "reddit:saved:t3_sav01"
    assert d.ts is None
    assert d.text is None
    assert d.title == "my first stew"  # derived from the permalink slug
    assert d.meta == {
        "type": "post",
        "subreddit": "synthcooking",
        "permalink": _PERMALINK,
    }


def test_saved_comment_maps_to_bookmark() -> None:
    permalink = f"{_PERMALINK}cmm77/"
    text = _SAVED_HEADER + f"cmm77,{permalink}\n"
    [d] = list(_parse_saved_comments(text.encode(), "saved_comments.csv"))
    assert d.external_id == "reddit:saved:t1_cmm77"
    assert d.title == "my first stew"
    assert d.meta["type"] == "comment"
    assert d.meta["subreddit"] == "synthcooking"


def test_saved_relative_permalink_still_yields_subreddit_and_title() -> None:
    """Old-generation exports used site-relative permalinks."""
    text = _SAVED_HEADER + "sav01,/r/fixturegardens/comments/abc12/tomato_towers/\n"
    [d] = list(_parse_saved_posts(text.encode(), "saved_posts.csv"))
    assert d.meta["subreddit"] == "fixturegardens"
    assert d.title == "tomato towers"


def test_saved_permalink_without_slug_has_no_title() -> None:
    text = _SAVED_HEADER + "sav01,https://www.reddit.com/comments/abc12/\n"
    [d] = list(_parse_saved_posts(text.encode(), "saved_posts.csv"))
    assert d.title is None
    assert "subreddit" not in d.meta
    assert d.meta["permalink"] == "https://www.reddit.com/comments/abc12/"


def test_saving_your_own_post_is_a_distinct_item() -> None:
    """Authored and saved identities live in separate namespaces: saving your
    own post yields a post item AND a bookmark item, never a collision."""
    post_row = f"abc12,{_PERMALINK},2023-06-15 14:30:00 UTC,,synthcooking,0,T,{_PERMALINK},x\n"
    [authored] = _posts(_POSTS_HEADER + post_row)
    saved_text = _SAVED_HEADER + f"abc12,{_PERMALINK}\n"
    [saved] = list(_parse_saved_posts(saved_text.encode(), "saved_posts.csv"))
    assert authored.external_id == "reddit:t3_abc12"
    assert saved.external_id == "reddit:saved:t3_abc12"


# ---------------------------------------------------------------------------
# Detection + parse() over archives
# ---------------------------------------------------------------------------


def test_detection_matches_export_layout_precisely(tmp_path: Path) -> None:
    matches = {
        # Reddit-unique anchor members, flat or nested one level down
        "subscribed_subreddits.csv": True,
        "saved_posts.csv": True,
        "saved_comments.csv": True,
        "export_synthetic_20260101/subscribed_subreddits.csv": True,
        "export_synthetic_20260101/saved_posts.csv": True,
        # generic names NEVER detect: the generic CSV ingester's (#150) and
        # other CSV exports' (#144 YNAB) territory
        "posts.csv": False,
        "comments.csv": False,
        "My Budget as of 2026-01-01 20-15 - Register.csv": False,
        "My Budget as of 2026-01-01 20-15 - Plan.csv": False,
        "unsaved_posts.csv": False,
        "resaved_posts.csv": False,
        "saved_posts.csv.bak": False,
    }
    plugin = discover()["reddit"]
    for name, expected in matches.items():
        assert plugin.detect.matches(name) is expected, name

    members = {
        "posts.csv": (_POSTS_HEADER).encode(),
        "subscribed_subreddits.csv": b"subreddit\nsynthcooking\n",
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    assert [p.name for p in detect_sources(open_archive(archive))] == ["reddit"]


def test_parse_reads_only_in_scope_members(tmp_path: Path) -> None:
    """Votes/telemetry CSVs share the archive; they must never become items."""
    members = {
        "posts.csv": (
            _POSTS_HEADER
            + f"abc12,{_PERMALINK},2023-06-15 14:30:00 UTC,,synthcooking,0,T,{_PERMALINK},hello\n"
        ).encode(),
        "saved_posts.csv": (_SAVED_HEADER + f"sav01,{_PERMALINK}\n").encode(),
        "post_votes.csv": (b"id,permalink,direction\nvote1,https://example.com/x,up\n"),
        "comment_votes.csv": b"id,permalink,direction\n",
        "subscribed_subreddits.csv": b"subreddit\nsynthcooking\n",
        "ip_logs.csv": b"date,ip\n",
        "checkfile.csv": b"filename,sha256\n",
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    drafts = list(parse(open_archive(archive), ParseContext()))
    assert sorted(d.external_id or "" for d in drafts) == [
        "reddit:saved:t3_sav01",
        "reddit:t3_abc12",
    ]


def test_parse_handles_nested_layout(tmp_path: Path) -> None:
    """A re-zipped export under a wrapper folder still parses."""
    root = "export_synthetic_20260101"
    members = {
        f"{root}/posts.csv": (
            _POSTS_HEADER
            + f"abc12,{_PERMALINK},2023-06-15 14:30:00 UTC,,synthcooking,0,T,{_PERMALINK},hello\n"
        ).encode(),
        f"{root}/subscribed_subreddits.csv": b"subreddit\n",
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    assert [p.name for p in detect_sources(open_archive(archive))] == ["reddit"]
    drafts = list(parse(open_archive(archive), ParseContext()))
    assert [d.external_id for d in drafts] == ["reddit:t3_abc12"]


def test_parse_empty_archive_yields_nothing(tmp_path: Path) -> None:
    archive = write_archive(tmp_path / "empty.zip", {"decoy/readme.md": b"x"}, "zip")
    assert list(parse(open_archive(archive), ParseContext())) == []
