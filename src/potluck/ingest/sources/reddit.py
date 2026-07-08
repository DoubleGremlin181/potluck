"""Reddit GDPR data-export source plugin.

Parses the flat zip of CSVs Reddit's data request (settings/data-request,
GDPR type) produces. Format spec (v1 authoritative; member set and column
headers verified against a real 2025 export — v0's Reddit ingester read the
same CSVs and its semantics port: authored content becomes items, votes and
telemetry never do):

- ``posts.csv``: ``id,permalink,date,ip,subreddit,gildings,title,url,body``
- ``comments.csv``: ``id,permalink,date,ip,subreddit,gildings,link,parent,
  body,media``
- ``saved_posts.csv`` / ``saved_comments.csv``: ``id,permalink`` only.
- Everything else in the export (votes, hidden posts, chats, message
  archives, subscriptions, ip_logs, statistics, …) is out of scope and never
  read.

Kind mapping (the canonical 12-kind vocabulary has no ``comment`` kind and
maps "social posts" → post):

- posts.csv / comments.csv → ``kind=post``; a comment is authored social
  content with ``title=None`` and ``meta.type = "comment"`` (posts carry
  ``meta.type = "post"``), the parent post's URL/fullname in ``meta.link`` /
  ``meta.parent``. Comments are deliberately not parent_id-chained: the
  parent almost never exists in the DB (you mostly comment on other people's
  posts, which the export does not contain).
- saved_*.csv → ``kind=bookmark``: the export carries no body, title, or
  date for saved content — only id + permalink — so the bookmark (the
  pointer, not the content) is the honest representation. ``title`` is
  derived from the permalink's slug segment (underscores → spaces) so
  bookmarks are FTS-findable (the index covers title/text only); the exact
  URL is preserved in ``meta.permalink``. Saved rows have no timestamp →
  ``ts=None``.

Column policy: ``subreddit``/``permalink`` (and ``url`` when its path
differs from the permalink's — link posts and crossposts; path comparison
because export generations disagree on absolute vs site-relative URLs) go
to meta; ``ip`` (telemetry, empty in modern
exports) and ``gildings`` (engagement count, same territory as the
out-of-scope votes files) are dropped; ``media`` passes through to meta when
non-empty (never observed non-empty in a real export; unknown semantics kept
verbatim rather than dropped).

Identity: Reddit ids are stable — ``reddit:t3_<id>`` (posts) /
``reddit:t1_<id>`` (comments) for authored content and
``reddit:saved:t3_<id>`` / ``reddit:saved:t1_<id>`` for saved, a separate
namespace so saving your own post yields two honest items instead of a
collision. Modern exports carry bare base36 ids; an id that already has a
``t<N>_`` fullname prefix (old-generation exports) is used as-is, never
double-prefixed.

Dates are ``YYYY-MM-DD HH:MM:SS UTC`` (accepted with or without the literal
``UTC`` suffix, always resolved as UTC). A malformed date keeps the row with
``ts=None`` plus one WARNING — the id is the identity, so content must not
be lost to a bad timestamp. CSV discipline: utf-8 with BOM tolerance,
quoted multiline bodies survive via stdlib csv, a member whose header lacks
the expected columns logs one WARNING and is skipped (a renamed/foreign
header must never misparse silently), csv errors are contained per member.

Detection is anchored on Reddit-unique member names
(``subscribed_subreddits.csv``, ``saved_posts.csv``, ``saved_comments.csv``)
— never on ``posts.csv``/``comments.csv``, which are generic enough to
collide with other CSV exports (#144 YNAB) and the generic ingesters'
territory (#150). Consequence: a hand-pruned archive containing ONLY
posts.csv/comments.csv is deliberately not detected.
"""

import csv
import io
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Final

from pydantic import JsonValue

from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import BookmarkDraft, PostDraft
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)

# Reddit-unique anchors only (module docstring); each alternative also
# matches one nesting level via '*/' for re-zipped exports ('*' crosses '/').
_EXPORT_GLOB = Glob(
    "subscribed_subreddits.csv|*/subscribed_subreddits.csv"
    "|saved_posts.csv|*/saved_posts.csv"
    "|saved_comments.csv|*/saved_comments.csv"
)

_TS_FORMATS: Final = ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d %H:%M:%S")

# An id that is already a fullname ("t3_abc12") keeps its prefix; bare base36
# ids never contain an underscore, so the match is unambiguous.
_FULLNAME_RE = re.compile(r"^t\d_")
_SUBREDDIT_RE = re.compile(r"/r/([^/]+)/")
_SLUG_RE = re.compile(r"/comments/[^/]+/([^/]+)")

# Columns whose absence means the member is not the format we know — the
# ignored columns (ip, gildings, media, link, parent, url) are deliberately
# not required, so their removal in a future export generation stays benign.
_POSTS_REQUIRED = frozenset({"id", "permalink", "date", "subreddit", "title", "body"})
_COMMENTS_REQUIRED = frozenset({"id", "permalink", "date", "subreddit", "body"})
_SAVED_REQUIRED = frozenset({"id", "permalink"})


def _read_rows(
    data: bytes, member_name: str, required: frozenset[str]
) -> Iterator[dict[str, str | None]]:
    """DictReader over one member with header validation and error containment.

    utf-8-sig strips a BOM if present; undecodable bytes are replaced, never
    fatal. A missing/foreign header logs one WARNING and yields nothing; a
    csv.Error mid-member logs one WARNING and stops that member (rows already
    yielded stand). A header-only member yields nothing silently — an empty
    account section is a legitimate state, not a parse failure.
    """
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    try:
        missing = required - set(reader.fieldnames or ())
        if missing:
            _logger.warning(
                "reddit: %r is missing expected column(s) %s — member skipped",
                member_name,
                sorted(missing),
            )
            return
        yield from reader
    except csv.Error as exc:
        _logger.warning("reddit: CSV error in %r: %s — remaining rows skipped", member_name, exc)


def _cell(row: dict[str, str | None], key: str) -> str:
    """One stripped cell value; missing/short-row cells collapse to ''."""
    return (row.get(key) or "").strip()


def _parse_ts(value: str, member_name: str, row_id: str) -> datetime | None:
    """Resolve one date cell; malformed values warn and become None (the id
    is the identity — content must survive a bad timestamp)."""
    if not value:
        return None
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    _logger.warning(
        "reddit: unrecognized date %r on row %r in %r — stored without timestamp",
        value,
        row_id,
        member_name,
    )
    return None


def _fullname(prefix: str, raw_id: str) -> str:
    return raw_id if _FULLNAME_RE.match(raw_id) else f"{prefix}_{raw_id}"


def _path_of(url: str) -> str:
    """The path part of *url*, scheme/host and trailing slash stripped.

    Export generations disagree on absolute vs site-relative reddit URLs
    (old permalinks are relative, urls absolute), so "does the url just
    restate the permalink?" must compare paths, not strings.
    """
    if "://" in url:
        url = url.split("://", 1)[1]
        url = url[url.find("/") :] if "/" in url else ""
    return url.rstrip("/")


def _row_id(row: dict[str, str | None], member_name: str) -> str | None:
    raw_id = _cell(row, "id")
    if not raw_id:
        _logger.warning("reddit: skipping row with no id in %r", member_name)
        return None
    return raw_id


def _parse_posts(data: bytes, member_name: str) -> Iterator[PostDraft]:
    for row in _read_rows(data, member_name, _POSTS_REQUIRED):
        raw_id = _row_id(row, member_name)
        if raw_id is None:
            continue
        permalink = _cell(row, "permalink")
        url = _cell(row, "url")
        meta: dict[str, JsonValue] = {"type": "post"}
        if subreddit := _cell(row, "subreddit"):
            meta["subreddit"] = subreddit
        if permalink:
            meta["permalink"] = permalink
        if url and _path_of(url) != _path_of(permalink):
            # A submitted target of its own (link posts, crossposts) — never
            # stored when the url merely restates the permalink (self posts).
            meta["url"] = url
        yield PostDraft(
            external_id=f"reddit:{_fullname('t3', raw_id)}",
            ts=_parse_ts(_cell(row, "date"), member_name, raw_id),
            title=_cell(row, "title") or None,
            text=(row.get("body") or "").strip() or None,
            meta=meta,
        )


def _parse_comments(data: bytes, member_name: str) -> Iterator[PostDraft]:
    for row in _read_rows(data, member_name, _COMMENTS_REQUIRED):
        raw_id = _row_id(row, member_name)
        if raw_id is None:
            continue
        meta: dict[str, JsonValue] = {"type": "comment"}
        if subreddit := _cell(row, "subreddit"):
            meta["subreddit"] = subreddit
        if permalink := _cell(row, "permalink"):
            meta["permalink"] = permalink
        if link := _cell(row, "link"):
            meta["link"] = link  # URL of the post commented on
        if parent := _cell(row, "parent"):
            meta["parent"] = parent  # fullname of the direct parent (t1_/t3_)
        if media := _cell(row, "media"):
            meta["media"] = media
        yield PostDraft(
            external_id=f"reddit:{_fullname('t1', raw_id)}",
            ts=_parse_ts(_cell(row, "date"), member_name, raw_id),
            title=None,
            text=(row.get("body") or "").strip() or None,
            meta=meta,
        )


def _parse_saved(
    data: bytes, member_name: str, *, prefix: str, saved_type: str
) -> Iterator[BookmarkDraft]:
    for row in _read_rows(data, member_name, _SAVED_REQUIRED):
        raw_id = _row_id(row, member_name)
        if raw_id is None:
            continue
        permalink = _cell(row, "permalink")
        meta: dict[str, JsonValue] = {"type": saved_type}
        title: str | None = None
        if permalink:
            meta["permalink"] = permalink
            if sub := _SUBREDDIT_RE.search(permalink):
                meta["subreddit"] = sub.group(1)
            if slug := _SLUG_RE.search(permalink):
                # The slug is the post title, lowercased and underscored —
                # derived display/search text; the exact URL stays in meta.
                title = slug.group(1).replace("_", " ").strip() or None
        yield BookmarkDraft(
            external_id=f"reddit:saved:{_fullname(prefix, raw_id)}",
            title=title,
            meta=meta,
        )


def _parse_saved_posts(data: bytes, member_name: str) -> Iterator[BookmarkDraft]:
    return _parse_saved(data, member_name, prefix="t3", saved_type="post")


def _parse_saved_comments(data: bytes, member_name: str) -> Iterator[BookmarkDraft]:
    return _parse_saved(data, member_name, prefix="t1", saved_type="comment")


@source(
    name="reddit",
    detect=_EXPORT_GLOB,
    kinds=(ItemKind.POST, ItemKind.BOOKMARK),
    parser_version=1,
)
def parse(archive: Archive, ctx: ParseContext) -> Iterator[PostDraft | BookmarkDraft]:
    """Yield drafts from the four in-scope members, one streaming pass.

    A single ``*.csv`` pattern pass keeps tar archives sequential; basename
    dispatch skips out-of-scope members (votes, telemetry) without reading
    them, so memory is bounded by one in-scope member (the corpora are small
    — a decade-heavy account is a few MB of CSV). ctx is part of the plugin
    contract but unused: there is nothing to parallelize.
    """
    for member, stream in archive.iter_members("*.csv"):
        base = member.name.rsplit("/", 1)[-1]
        if base == "posts.csv":
            yield from _parse_posts(stream.read(), member.name)
        elif base == "comments.csv":
            yield from _parse_comments(stream.read(), member.name)
        elif base == "saved_posts.csv":
            yield from _parse_saved_posts(stream.read(), member.name)
        elif base == "saved_comments.csv":
            yield from _parse_saved_comments(stream.read(), member.name)
