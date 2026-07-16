"""Generic image-folder source plugin (#150): point potluck at any folder
(or archive) of pictures — EXIF metadata becomes photo items + media rows.

GENERIC TIER: this plugin only applies when no specific source matched the
archive (see detect_sources). Degradation to know about: a messy folder
holding both a recognized export (a Google Photos Takeout, a WhatsApp chat
with its media) and loose pictures imports only the export — the detection
listing shows which source won; importing the pictures subfolder directly is
the escape hatch.

Detection: the Pillow-core extensions ``jpg|jpeg|png|webp|gif|tiff|bmp``,
each in lower AND upper case (DCIM naming is uppercase on most cameras —
``IMG_0001.JPG``). Deliberately absent: HEIC/HEIF (Pillow needs a plugin
dependency — non-goal until it can be a core dependency, absolute rule 2),
``.tif`` (the brief's seven only; the probe itself handles it if ever added),
and video extensions (nothing can probe them yet — P6).

Identity / dedup: ``images:<sha256(bytes)[:16]>`` — the photos (#149)
posture: byte-identical files ARE the same picture, so a repeated hash
within one run re-yields the FIRST occurrence's draft verbatim (the engine
counts an exact duplicate; the first path wins the title). occurrence_suffix
is deliberately unused — one draft per member means a per-member counter
could never exceed 1, and numbering cross-member copies would mint phantom
items. The same picture inside a Google Photos Takeout AND a loose folder is
two items (identity is per-source by schema; documented, not fought).

Field policy: ts = EXIF DateTimeOriginal (naive read as UTC — the
whatsapp/gmail unknown-zone policy — unless OffsetTimeOriginal is present)
→ member mtime (zip/tar/directory modification time; epoch-0 and negative
read as absent) → None. lat/lon/gps_alt = EXIF GPS, rejecting the 0,0 Null
Island sentinel and out-of-range values. title = the filename. text = None
(an arbitrary image carries no prose). meta = {type: photo} only — no album
or folder name: paths are identity-free here and folder names are noise
compared to Takeout's curated albums. Satellite: the media table (migration
014 — reused, no new migration) via the PhotoDraft writer.

Containment — the deliberate difference from photos: an unreadable file
with an image extension is SKIPPED with a per-file warning, never imported
blind. Arbitrary folders contain misnamed junk with no sidecar attesting
"this is media" (photos imports byte facts because Takeout's sidecar does).

Memory posture: one streaming pass per member — sha256 + size over 1 MiB
chunks, buffering only the first 32 MiB for the Pillow header probe (the
photos ceiling); the first-occurrence draft cache is bounded by the unique
image count.
"""

import logging
import mimetypes
from collections.abc import Iterator
from typing import Final

from potluck.ingest.imagemeta import Probe, hash_and_head, probe_image
from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive, mtime_ts
from potluck.models.drafts import PhotoDraft
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)

_DIGEST_CHARS: Final = 16  # the chrome/timeline/photos identity sizing

# Lower + upper alternatives ('|' is the Glob any-of separator); fnmatch is
# case-sensitive by design (virtual posix paths), so both spellings are
# spelled out rather than case-folding the rule away.
_LOWER_EXTS: Final = ("jpg", "jpeg", "png", "webp", "gif", "tiff", "bmp")
_EXPORT_GLOB = Glob(
    "|".join(f"*.{ext}" for ext in _LOWER_EXTS)
    + "|"
    + "|".join(f"*.{ext.upper()}" for ext in _LOWER_EXTS)
)


@source(
    name="images",
    detect=_EXPORT_GLOB,
    kinds=(ItemKind.PHOTO,),
    parser_version=1,
    generic=True,
)
def parse(archive: Archive, ctx: ParseContext) -> Iterator[PhotoDraft]:
    """Yield one PhotoDraft per readable image member, streaming.

    One pass over all members ('*' then Glob-filter — a second archive walk
    per extension would be tar-hostile); every matched extension is Pillow-
    probeable by construction. ctx is part of the plugin contract but
    unused: the work is I/O-bound streaming.
    """
    first_draft_by_sha: dict[str, PhotoDraft] = {}

    for member, stream in archive.iter_members("*"):
        if not _EXPORT_GLOB.matches(member.name):
            continue
        basename = member.name.rsplit("/", 1)[-1]
        sha256, size, head = hash_and_head(stream, probeable=True)

        cached = first_draft_by_sha.get(sha256)
        if cached is not None:
            # Byte-identical copy elsewhere in the folder tree: re-yield the
            # first occurrence verbatim so the engine sees an exact duplicate.
            yield cached
            continue

        try:
            probe: Probe = probe_image(head)
        except Exception as exc:  # noqa: BLE001 — Pillow's broad surface; see imagemeta
            _logger.warning("images: %r is not a readable image (%s) — skipped", member.name, exc)
            continue

        draft = PhotoDraft(
            external_id=f"images:{sha256[:_DIGEST_CHARS]}",
            ts=probe.taken or mtime_ts(member),
            title=basename,
            lat=probe.gps[0] if probe.gps is not None else None,
            lon=probe.gps[1] if probe.gps is not None else None,
            width=probe.width,
            height=probe.height,
            camera_make=probe.make,
            camera_model=probe.model,
            gps_alt=probe.gps[2] if probe.gps is not None else None,
            mime=probe.mime or mimetypes.guess_type(basename)[0],
            size_bytes=size,
            sha256=sha256,
            meta={"type": "photo"},
        )
        first_draft_by_sha[sha256] = draft
        yield draft
