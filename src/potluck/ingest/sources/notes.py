"""Generic text/markdown notes source plugin (#150): point potluck at any
folder of loose notes — ``.txt`` / ``.md`` / ``.markdown`` become note items.

GENERIC TIER: this plugin only applies when no specific source matched the
archive (see detect_sources). Degradation to know about: a folder holding
both a recognized export (e.g. a WhatsApp ``WhatsApp Chat with X.txt``) and
loose notes imports only the export — the detection listing shows which
source won; importing the notes subfolder (or a single file) directly is the
escape hatch.

Identity policy — the deliberate contrast with images (#150 design): the
RELATIVE MEMBER PATH (``notes:<path>``), because a note file is a living
document — an edit must UPDATE the item in place (the engine identity path;
same posture as timeline re-inference), whereas byte identity would mint a
new item per save. The cost, documented rather than hidden: a rename creates
a new item and orphans the old one (#153 rm/forget is the eventual answer),
and the same file imported once via its parent folder and once directly gets
two identities (the member path differs — SingleFileArchive exposes the bare
basename). Occurrence suffixes are unnecessary: member paths are unique
within an archive by construction.

Field policy: title = the first non-empty ATX H1 line (``# Title``) for
markdown files, else the filename stem (txt always uses the stem — a ``#``
line in plain text is not a heading). text = the full file content through
the established textclean posture (#199), decoded utf-8 with
``errors="replace"`` (arbitrary folders contain arbitrary encodings; a
replacement character beats a crashed import — documented lossiness) and a
BOM stripped. ts = member mtime (epoch-0/negative read as absent) → None.
A 0-byte file still yields a titled, textless note: the filename itself is
information. No satellite table; meta stays empty.

Size guard: members over MAX_NOTE_BYTES skip with a warning — arbitrary
folders contain stray logs/database dumps with note extensions, and one
10 MiB+ blob is never a note a human wrote (the largest legitimate bodies
observed anywhere in potluck are under 256 KiB — textclean's cap). The
check uses the member's declared size, so oversized members are never read.
"""

import logging
from collections.abc import Iterator
from typing import Final

from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive, mtime_ts
from potluck.ingest.textclean import clean_text
from potluck.models.drafts import NoteDraft
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)

_EXPORT_GLOB = Glob("*.txt|*.md|*.markdown")
_MD_EXTS: Final = (".md", ".markdown")

# Skip cap (module docstring): 40x textclean's 256 KiB body cap — generous
# for any human-written note, small enough to reject stray logs outright.
MAX_NOTE_BYTES: Final = 10 * 1024 * 1024


def _h1_title(text: str) -> str | None:
    """The first non-empty ATX H1 (``# Title``): CommonMark requires the
    space, and ``##``+ are sub-headings, not titles."""
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            if title:
                return title
    return None


@source(
    name="notes",
    detect=_EXPORT_GLOB,
    kinds=(ItemKind.NOTE,),
    parser_version=1,
    generic=True,
)
def parse(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
    """Yield one NoteDraft per text/markdown member, streaming.

    One pass over all members ('*' then Glob-filter); memory is bounded by
    one decoded file (≤ MAX_NOTE_BYTES). ctx is part of the plugin contract
    but unused: there is nothing to parallelize.
    """
    for member, stream in archive.iter_members("*"):
        if not _EXPORT_GLOB.matches(member.name):
            continue
        if member.size > MAX_NOTE_BYTES:
            _logger.warning(
                "notes: %r is %d bytes (cap %d) — not a note, skipped",
                member.name,
                member.size,
                MAX_NOTE_BYTES,
            )
            continue

        text = clean_text(stream.read().decode("utf-8-sig", errors="replace"))
        basename = member.name.rsplit("/", 1)[-1]
        stem = basename.rsplit(".", 1)[0]
        title = (_h1_title(text) if basename.endswith(_MD_EXTS) else None) or stem

        yield NoteDraft(
            external_id=f"notes:{member.name}",
            ts=mtime_ts(member),
            title=title,
            text=text or None,
        )
