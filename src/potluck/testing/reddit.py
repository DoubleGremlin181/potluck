"""Deterministic synthetic Reddit GDPR export generator.

Ships inside ``potluck.testing`` so tests, committed fixtures, and bench
scenarios share one deterministic source. Same arguments → identical bytes on
every machine, forever. Never put real personal data here — subreddits are
fixture names, titles/bodies come from the shared WORDS list, ids are
sequential synthetic tokens.

Row shapes are modular rules of the row index ``i`` (not RNG draws), so
expected parser outcomes have exact closed forms — every generated row
parses to exactly one draft (see :func:`expected_item_counts`):

- posts: ``i % 9 == 6`` → link post (empty body, external ``url``; see
  :func:`expected_link_post_count`); otherwise a self post
  (``url == permalink``)
- bodies (posts + comments, when non-empty): ``i % 7 == 2`` → multiline
  (embedded newlines, csv-quoted); ``i % 5 == 1`` → embedded commas and
  double quotes; ``i % 10 == 4`` → emoji suffix
- comments: ``i % 4 == 3`` → empty ``parent`` (as in real exports);
  otherwise ``t3_``/``t1_`` fullname parents alternate

``posts.csv`` is written with a UTF-8 BOM — BOM tolerance is part of the
parser contract, baked into every fixture. The member set mirrors the real
export shape: the four in-scope CSVs plus the ``subscribed_subreddits.csv``
detection anchor and a ``post_votes.csv`` decoy the parser must never read.

Regenerate the golden fixture::

    python -c "
    from pathlib import Path
    from potluck.testing.reddit import write_reddit_export
    write_reddit_export(Path('tests/fixtures/reddit'), posts=24, comments=30,
                        saved_posts=6, saved_comments=5, seed=11, fmt='dir')
    "
"""

import csv
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from potluck.testing.archives import write_archive
from potluck.testing.generators import WORDS

_BASE_TS = datetime(2022, 8, 5, 12, 0, 0, tzinfo=UTC)

_SUBREDDITS = ("synthcooking", "fixturegardens", "testtrains", "mockastronomy")
_EMOJI = ("🎉", "🚀", "🥘", "✨")

_POSTS_HEADER = ["id", "permalink", "date", "ip", "subreddit", "gildings", "title", "url", "body"]
_COMMENTS_HEADER = [
    "id",
    "permalink",
    "date",
    "ip",
    "subreddit",
    "gildings",
    "link",
    "parent",
    "body",
    "media",
]
_SAVED_HEADER = ["id", "permalink"]


def expected_item_counts(
    *, posts: int = 0, comments: int = 0, saved_posts: int = 0, saved_comments: int = 0
) -> dict[str, int]:
    """Items-by-kind the parser yields for one generated export (every row
    parses to exactly one draft; zero-count kinds are omitted)."""
    counts: dict[str, int] = {}
    if posts or comments:
        counts["post"] = posts + comments
    if saved_posts or saved_comments:
        counts["bookmark"] = saved_posts + saved_comments
    return counts


def expected_link_post_count(posts: int) -> int:
    """Generated posts whose url differs from their permalink (meta.url)."""
    return sum(1 for i in range(posts) if i % 9 == 6)


def _words(salt: int, i: int, offset: int, k: int) -> str:
    return " ".join(WORDS[(salt + i * 7 + (offset + j) * 3) % len(WORDS)] for j in range(k))


def _date(i: int) -> str:
    dt = _BASE_TS + timedelta(hours=7 * i, minutes=(i * 13) % 60)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _sub(salt: int, i: int) -> str:
    return _SUBREDDITS[(salt + i) % len(_SUBREDDITS)]


def _slug(salt: int, i: int, offset: int) -> str:
    return _words(salt, i, offset, 3).replace(" ", "_")


def _body(i: int, salt: int, offset: int) -> str:
    """One body per the modular shape rules (never called for link posts)."""
    text = _words(salt, i, offset, 5 + i % 4)
    if i % 7 == 2:
        text = f"{text}\n{_words(salt, i, offset + 40, 3)}\n\n{_words(salt, i, offset + 60, 4)}"
    elif i % 5 == 1:
        text = f'{text}, they said "{_words(salt, i, offset + 40, 2)}", twice'
    if i % 10 == 4:
        text += " " + _EMOJI[i % len(_EMOJI)]
    return text


def _csv_bytes(header: list[str], rows: list[list[str]], *, bom: bool = False) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return (("\ufeff" if bom else "") + buf.getvalue()).encode("utf-8")


def _post_row(i: int, salt: int) -> list[str]:
    sub = _sub(salt, i)
    permalink = f"https://www.reddit.com/r/{sub}/comments/p{i:03x}/{_slug(salt, i, 0)}/"
    if i % 9 == 6:  # link post: external url, no body
        url, body = f"https://example.com/article-{salt}-{i}", ""
    else:
        url, body = permalink, _body(i, salt, 10)
    title = _words(salt, i, 5, 4)
    return [f"p{i:03x}", permalink, _date(i), "", sub, "0", title, url, body]


def _comment_row(i: int, salt: int) -> list[str]:
    sub = _sub(salt, i + 1)
    link = f"https://www.reddit.com/r/{sub}/comments/x{i:03x}/{_slug(salt, i, 20)}/"
    permalink = f"{link}c{i:03x}/"
    if i % 4 == 3:
        parent = ""
    elif i % 2 == 0:
        parent = f"t3_x{i:03x}"
    else:
        parent = f"t1_c9{i:02x}"
    return [f"c{i:03x}", permalink, _date(i), "", sub, "0", link, parent, _body(i, salt, 30), ""]


def _saved_post_row(i: int, salt: int) -> list[str]:
    sub = _sub(salt, i + 2)
    permalink = f"https://www.reddit.com/r/{sub}/comments/sp{i:03x}/{_slug(salt, i, 50)}/"
    return [f"sp{i:03x}", permalink]


def _saved_comment_row(i: int, salt: int) -> list[str]:
    sub = _sub(salt, i + 3)
    permalink = f"https://www.reddit.com/r/{sub}/comments/sx{i:03x}/{_slug(salt, i, 70)}/sc{i:03x}/"
    return [f"sc{i:03x}", permalink]


def reddit_members(
    *,
    posts: int = 0,
    comments: int = 0,
    saved_posts: int = 0,
    saved_comments: int = 0,
    seed: int = 42,
) -> dict[str, bytes]:
    """The member set of one synthetic export ({posix_name: content})."""
    salt = seed * 1009
    return {
        "posts.csv": _csv_bytes(
            _POSTS_HEADER, [_post_row(i, salt) for i in range(posts)], bom=True
        ),
        "comments.csv": _csv_bytes(
            _COMMENTS_HEADER, [_comment_row(i, salt) for i in range(comments)]
        ),
        "saved_posts.csv": _csv_bytes(
            _SAVED_HEADER, [_saved_post_row(i, salt) for i in range(saved_posts)]
        ),
        "saved_comments.csv": _csv_bytes(
            _SAVED_HEADER, [_saved_comment_row(i, salt) for i in range(saved_comments)]
        ),
        # Detection anchor (out of parse scope, like the real member).
        "subscribed_subreddits.csv": _csv_bytes(["subreddit"], [[name] for name in _SUBREDDITS]),
        # Out-of-scope decoy: the parser must never read votes.
        "post_votes.csv": _csv_bytes(
            ["id", "permalink", "direction"],
            [["v000", "https://www.reddit.com/r/synthcooking/comments/v000/decoy_vote/", "up"]],
        ),
    }


def write_reddit_export(
    dest_dir: Path,
    *,
    posts: int = 0,
    comments: int = 0,
    saved_posts: int = 0,
    saved_comments: int = 0,
    seed: int = 42,
    fmt: Literal["zip", "tgz", "dir"] = "zip",
) -> Path:
    """Materialise a synthetic Reddit GDPR export archive in *dest_dir*.

    Returns the archive path (or the directory root for ``fmt="dir"``).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    members = reddit_members(
        posts=posts,
        comments=comments,
        saved_posts=saved_posts,
        saved_comments=saved_comments,
        seed=seed,
    )
    if fmt == "dir":
        dest = dest_dir / "reddit-synth-001"
        write_archive(dest, members, "dir")
        return dest
    ext = "zip" if fmt == "zip" else "tgz"
    dest = dest_dir / f"reddit-synth-001.{ext}"
    write_archive(dest, members, fmt)
    return dest
