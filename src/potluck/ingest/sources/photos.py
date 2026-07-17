"""Google Photos source plugin: sidecar JSON + EXIF → photo items + media satellite.

Format spec (v1 authoritative; layout, sidecar schema, and every claim below
verified against a real 2025-12 Takeout part-12, shape only — 2,783 media +
2,783 sidecars across two albums):

- Layout: ``Takeout/Google Photos/<album>/`` holds media files (real mix:
  jpg/jpeg/mp4/png/webp) plus one ``<media>.supplemental-metadata.json``
  sidecar each; user-named albums additionally carry an album-level
  ``metadata.json`` (``title`` + ``sharedAlbumComments``). Product-level
  jsons directly under ``Google Photos/`` (print-subscriptions style) are
  skipped silently.
- **Sidecar naming — the hazard**: Takeout caps the json STEM (name minus
  ``.json`` and any ``(N)``) at exactly 46 chars — all 208 real truncated
  names measure 46 — cutting ``supplemental-metadata`` mid-word, down to a
  bare ``..json``, and even into the media extension (3 real cases). Pairing
  rule: the stem must be a PREFIX of ``<media>.supplemental-metadata`` for a
  media file in the SAME directory; the longest matching stem wins (100%
  pairing coverage on the real export, zero ambiguity). The ``(N)``
  pathology: ``X.jpg.supplemental-metadata(1).json`` belongs to ``X(1).jpg``
  — the (N) transfers to just before the media extension (3 real cases, all
  resolving this way). Truncation COLLISIONS (two media sharing the first 46
  chars of their target, so both sidecars collapse to one stem and only the
  second sidecar's *filename* gets an ``(N)``): the sidecar ``title`` field
  — the untruncated original filename — disambiguates within a stem group
  before the (N)-slot rule, and a sidecar claimed by a second distinct
  basename warns ("metadata may be mis-assigned") instead of mis-pairing
  silently (zero real instances; review fix cycle 1). ``X-edited.jpg``
  variants never prefix-match the base sidecar and import sidecar-less
  (zero -edited files in the real export; the parser does not guess at
  sharing).
- Sidecar schema (stable across all 2,783): ``title`` (original filename),
  ``description`` (always empty in the real export), ``imageViews``,
  ``creationTime``/``photoTakenTime`` (epoch-string blocks), ``geoData``
  (floats; **0.0/0.0 = "no GPS" sentinel — treated as absent, lat/lon 0,0 is
  never emitted**), optional ``geoDataExif`` (real: a strict subset of
  usable geoData, agreeing to 1e-4 in all 1,108 shared cases), optional
  ``people[]{name}``, ``url``, ``googlePhotosOrigin`` (mobileUpload with
  deviceFolder.localFolderName, or a rare ``composition`` variant),
  optional ``appSource{androidPackageName}``, optional ``favorited``.

Kind mapping: the locked 12-kind vocabulary has PHOTO and no VIDEO, so
photos AND videos are ``kind=photo`` with ``meta.type`` = photo | video (the
post/comment and visit/route meta.type resolution). Satellite: the media
table (migration 014) carries width/height, camera make/model, gps_alt,
mime, size_bytes, sha256 — no files rows: the item IS its file, and archive
member paths are transient across re-exports, so the byte hash is the only
stored locator (P6 pixel ingestion finds blobs by sha256).

Identity / cross-album dedup: ``photos:<sha256(media bytes)[:16]>`` — bytes
are the one thing stable across album copies AND re-exports (sidecar title
and album differ). A repeated hash within one run re-yields the FIRST
occurrence's draft verbatim, so the engine counts an exact duplicate and the
first album wins meta even if the two albums' sidecars drift (never
observed; the real export has ZERO byte-duplicates — the acceptance pair
lives in the generator). occurrence_suffix is deliberately unused:
byte-identical media IS the same photo by definition, so numbering
duplicates would mint phantom items (deviation from the whatsapp fingerprint
posture, where identical text blocks are distinct events).

Field precedence, documented: ts = photoTakenTime → EXIF DateTimeOriginal
(naive read as UTC — the whatsapp/gmail unknown-zone policy — unless
OffsetTimeOriginal is present) → creationTime (upload time; real export:
photoTakenTime on all 2,783, so the fallbacks are defensive). lat/lon =
geoData → geoDataExif → in-file EXIF GPS, each level rejecting the 0,0
sentinel and out-of-range values; gps_alt rides along from the winning
source. title = sidecar title (the untruncated original filename), fallback
media basename. text = description + "With <people names>". meta: type,
album (metadata.json title, else the album directory name), url (own-data
posture: it locates the photo in Google Photos), device_folder + app_source
(provenance: camera roll vs app-saved), favorited only when true.
imageViews (engagement telemetry) and sharedAlbumComments (another person's
comments on a shared album) are skipped — revisit if a real need appears.

Memory posture: pass 1 holds every sidecar as a small parsed summary (a few
hundred bytes each — the google_chat sidecar bound); pass 2 streams each
media member in 1 MiB chunks through sha256, buffering only the first
32 MiB of PROBEABLE images for Pillow (header + EXIF live at the front of
JPEG/PNG; the cap covers every real photo). Videos and unknown extensions
hash with zero buffering and are never probed (mp4 dimensions/duration stay
NULL until a video probe dependency exists — P6). The first-occurrence
draft cache is bounded by the unique-photo count (same order as the sidecar
map). A malformed image warns and imports from byte facts alone; Pillow's
failure surface is genuinely broad (UnidentifiedImageError, OSError,
ValueError, SyntaxError, ZeroDivisionError on 0-denominator rationals), so
the probe containment is a documented blanket except.

Detection anchors on the DIRECTORY — any member under ``Google Photos/``
(truncation can destroy every sidecar-name pattern, but the media files
themselves always exist). ``Google Play*`` products, paths merely containing
"Photos" (``Drive/My Photos/…``), and ``archive_browser.html`` never match.
"""

import json
import logging
import mimetypes
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from pydantic import JsonValue

from potluck.ingest.imagemeta import PROBE_EXTS, Probe, extension, hash_and_head, probe_image
from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import PhotoDraft
from potluck.models.items import ItemKind

_logger = logging.getLogger(__name__)

# Directory-anchored detection (module docstring): '*/' ('*' crosses '/')
# covers Takeout/ nesting and re-zipped deeper layouts, the bare alternative
# a root-relative Google Photos/ folder. Both require the exact
# 'Google Photos/' segment, so 'Google Play Store/…' and '…My Photos/…'
# never match.
_EXPORT_GLOB = Glob("Google Photos/*|*/Google Photos/*")

_PRODUCT_SEGMENT: Final = "Google Photos"
_SIDECAR_SUFFIX: Final = ".supplemental-metadata"
_DIGEST_CHARS: Final = 16  # the chrome/timeline identity sizing

# Sidecar json name: optional '(N)' between the (possibly truncated) stem
# and '.json'. Media name: optional '(N)' just before the extension.
_JSON_NAME_RE: Final = re.compile(r"^(?P<stem>.*?)(?:\((?P<n>\d+)\))?\.json$")
_MEDIA_N_RE: Final = re.compile(r"^(?P<root>.+)\((?P<n>\d+)\)(?P<ext>\.[^.]+)$")
_METADATA_STEM: Final = "metadata"


@dataclass(slots=True)
class _Sidecar:
    """One parsed supplemental-metadata sidecar (pass-1 summary)."""

    member_name: str
    title: str | None = None
    description: str | None = None
    taken: datetime | None = None
    creation: datetime | None = None
    geo: tuple[float, float, float | None] | None = None
    geo_exif: tuple[float, float, float | None] | None = None
    people: tuple[str, ...] = ()
    favorited: bool = False
    url: str | None = None
    device_folder: str | None = None
    app_source: str | None = None
    n: int | None = None  # the sidecar filename's own (N), before .json
    claimed_by: str | None = None  # first media basename that paired with it


@dataclass(slots=True)
class _DirState:
    """Per-directory pass-2 state: the sidecar lookup + warning latch.

    sidecars is keyed by STEM alone, each group holding every (N) variant of
    that stem: truncation collisions collapse two DIFFERENT media's sidecars
    onto one 46-char stem (review fix cycle 1), so the (N) variants must be
    selectable together, not only through the media name's own (N).
    """

    sidecars: dict[str, list[_Sidecar]] = field(default_factory=dict)
    orphan_warned: bool = False


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _rel_after_product(member_name: str) -> list[str]:
    """Path segments after the first 'Google Photos' segment (detection
    guarantees one exists)."""
    parts = member_name.split("/")
    return parts[parts.index(_PRODUCT_SEGMENT) + 1 :]


# ---------------------------------------------------------------------------
# Sidecar parsing (pass 1)
# ---------------------------------------------------------------------------


def _parse_epoch(block: object) -> datetime | None:
    """A {timestamp: epoch-string} block → aware UTC instant, or None for
    any foreign shape (bool is an int subclass and still foreign)."""
    if not isinstance(block, dict):
        return None
    raw = block.get("timestamp")
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _parse_geo(block: object) -> tuple[float, float, float | None] | None:
    """A geoData/geoDataExif block → (lat, lon, alt), or None.

    0.0/0.0 is the exporter's "no GPS" sentinel (1,675 of the real 2,783)
    and reads as absent — lat/lon 0,0 must NEVER be emitted; out-of-range
    and non-numeric values are rejected the same way.
    """
    if not isinstance(block, dict):
        return None
    lat, lon = block.get("latitude"), block.get("longitude")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (lat, lon)):
        return None
    assert isinstance(lat, (int, float)) and isinstance(lon, (int, float))  # narrowed above
    lat, lon = float(lat), float(lon)
    if lat == 0.0 and lon == 0.0:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    alt_raw = block.get("altitude")
    alt = (
        float(alt_raw)
        if isinstance(alt_raw, (int, float)) and not isinstance(alt_raw, bool)
        else None
    )
    return lat, lon, alt


def _parse_sidecar(data: bytes, member_name: str) -> _Sidecar | None:
    """Parse one sidecar json; malformed input warns and returns None (the
    media still imports, just sidecar-less)."""
    try:
        doc: object = json.loads(data.decode("utf-8-sig", errors="replace"))
    except json.JSONDecodeError as exc:
        _logger.warning("photos: unreadable sidecar %r: %s", member_name, exc)
        return None
    if not isinstance(doc, dict):
        _logger.warning("photos: sidecar %r is not an object — ignored", member_name)
        return None

    people: list[str] = []
    raw_people: object = doc.get("people")
    if isinstance(raw_people, list):
        for entry in raw_people:
            name = _str_or_none(entry.get("name")) if isinstance(entry, dict) else None
            if name is not None:
                people.append(name)

    origin: object = doc.get("googlePhotosOrigin")
    mobile: object = origin.get("mobileUpload") if isinstance(origin, dict) else None
    folder: object = mobile.get("deviceFolder") if isinstance(mobile, dict) else None
    device_folder = (
        _str_or_none(folder.get("localFolderName")) if isinstance(folder, dict) else None
    )
    app: object = doc.get("appSource")
    app_source = _str_or_none(app.get("androidPackageName")) if isinstance(app, dict) else None

    raw_description = doc.get("description")
    description = raw_description.strip() if isinstance(raw_description, str) else ""

    return _Sidecar(
        member_name=member_name,
        title=_str_or_none(doc.get("title")),
        description=description or None,
        taken=_parse_epoch(doc.get("photoTakenTime")),
        creation=_parse_epoch(doc.get("creationTime")),
        geo=_parse_geo(doc.get("geoData")),
        geo_exif=_parse_geo(doc.get("geoDataExif")),
        people=tuple(people),
        favorited=doc.get("favorited") is True,
        url=_str_or_none(doc.get("url")),
        device_folder=device_folder,
        app_source=app_source,
    )


def _parse_album_title(data: bytes, member_name: str) -> str | None:
    """The album metadata.json title, or None (warned). sharedAlbumComments
    are deliberately not items (module docstring)."""
    try:
        doc: object = json.loads(data.decode("utf-8-sig", errors="replace"))
    except json.JSONDecodeError as exc:
        _logger.warning(
            "photos: unreadable album metadata %r: %s — directory name used", member_name, exc
        )
        return None
    return _str_or_none(doc.get("title")) if isinstance(doc, dict) else None


# ---------------------------------------------------------------------------
# Pairing (pass 2)
# ---------------------------------------------------------------------------


def _lookup_prefix_group(sidecars: dict[str, list[_Sidecar]], target: str) -> list[_Sidecar] | None:
    """The longest stem that is a prefix of *target* — its whole (N)-variant
    group — walking lengths downward. Length-agnostic, so any truncation
    depth pairs (the 46-char cap is an observation, not an assumption)."""
    for length in range(len(target), 0, -1):
        group = sidecars.get(target[:length])
        if group:
            return group
    return None


def _select_sidecar(group: list[_Sidecar], media_basename: str, n: int | None) -> _Sidecar | None:
    """Pick one sidecar from a stem group for *media_basename* (whose own
    (N) is *n*, None for plain names).

    Title first (review fix cycle 1): the sidecar ``title`` field carries the
    UNTRUNCATED original filename, so a title equal to the media basename is
    exact evidence — it narrows the pool before the (N)-slot rule. Within
    the pool the sidecar whose own (N) matches the media's wins (the
    canonical slot: None for plain names, N for transfer). A single
    title-narrowed leftover wins even from the "wrong" slot — that is
    exactly the 46-char collision, where media B's sidecar sits in the (1)
    slot while B's name carries no (N).
    """
    titled = [s for s in group if s.title == media_basename]
    for sidecar in titled or group:
        if sidecar.n == n:
            return sidecar
    if len(titled) == 1:
        return titled[0]
    return None


def _match_sidecar(state: _DirState, media_basename: str) -> _Sidecar | None:
    """The pairing rule (module docstring): direct prefix match first, then
    the (N) transfer for ``X(1).jpg`` ← ``X.jpg.supplemental-metadata(1)``."""
    group = _lookup_prefix_group(state.sidecars, media_basename + _SIDECAR_SUFFIX)
    if group is not None:
        direct = _select_sidecar(group, media_basename, None)
        if direct is not None:
            return direct
    m = _MEDIA_N_RE.match(media_basename)
    if m is not None:
        base = m.group("root") + m.group("ext")
        base_group = _lookup_prefix_group(state.sidecars, base + _SIDECAR_SUFFIX)
        if base_group is not None:
            return _select_sidecar(base_group, media_basename, int(m.group("n")))
    return None


# ---------------------------------------------------------------------------
# EXIF probing (shared implementation: potluck.ingest.imagemeta, #150)
# ---------------------------------------------------------------------------


def _probe_image(head: bytes, member_name: str) -> Probe:
    """Containment wrapper over the shared probe: a malformed image must
    never kill the import — it warns and imports from byte facts alone
    (module docstring; the images source makes the opposite call and skips).
    """
    try:
        return probe_image(head)
    except Exception as exc:  # noqa: BLE001 — Pillow's broad surface; see imagemeta
        _logger.warning(
            "photos: %r is not a readable image (%s) — imported from byte facts alone",
            member_name,
            exc,
        )
        return Probe()


# ---------------------------------------------------------------------------
# Media streaming
# ---------------------------------------------------------------------------


def _compose_text(sidecar: _Sidecar | None) -> str | None:
    if sidecar is None:
        return None
    parts = []
    if sidecar.description is not None:
        parts.append(sidecar.description)
    if sidecar.people:
        parts.append("With " + ", ".join(sidecar.people))
    return "\n".join(parts) or None


def _build_draft(
    basename: str,
    album: str | None,
    sidecar: _Sidecar | None,
    probe: Probe,
    sha256: str,
    size: int,
) -> PhotoDraft:
    """Assemble one media member's draft (precedence rules in the module
    docstring)."""
    mime = probe.mime or mimetypes.guess_type(basename)[0]
    ts = (
        (sidecar.taken if sidecar is not None else None)
        or probe.taken
        or (sidecar.creation if sidecar is not None else None)
    )
    geo = (
        (sidecar.geo if sidecar is not None else None)
        or (sidecar.geo_exif if sidecar is not None else None)
        or probe.gps
    )
    title = (sidecar.title if sidecar is not None else None) or basename

    meta: dict[str, JsonValue] = {"type": "video" if (mime or "").startswith("video/") else "photo"}
    if album is not None:
        meta["album"] = album
    if sidecar is not None:
        if sidecar.favorited:
            meta["favorited"] = True
        if sidecar.url is not None:
            meta["url"] = sidecar.url
        if sidecar.device_folder is not None:
            meta["device_folder"] = sidecar.device_folder
        if sidecar.app_source is not None:
            meta["app_source"] = sidecar.app_source

    return PhotoDraft(
        external_id=f"photos:{sha256[:_DIGEST_CHARS]}",
        ts=ts,
        title=title,
        text=_compose_text(sidecar),
        lat=geo[0] if geo is not None else None,
        lon=geo[1] if geo is not None else None,
        width=probe.width,
        height=probe.height,
        camera_make=probe.make,
        camera_model=probe.model,
        gps_alt=geo[2] if geo is not None else None,
        mime=mime,
        size_bytes=size,
        sha256=sha256,
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Passes
# ---------------------------------------------------------------------------


def _collect_sidecars(archive: Archive) -> tuple[dict[str, _DirState], dict[str, str]]:
    """Pass 1: every json under Google Photos/ → per-directory sidecar maps
    (keyed by (stem, N)) + album titles from metadata.json."""
    dirs: dict[str, _DirState] = {}
    album_titles: dict[str, str] = {}
    for member, stream in archive.iter_members(f"*{_PRODUCT_SEGMENT}/*.json"):
        if not _EXPORT_GLOB.matches(member.name):
            continue
        rel = _rel_after_product(member.name)
        if len(rel) < 2:
            continue  # product-level json (print-subscriptions style): documented non-item
        directory, basename = member.name.rsplit("/", 1)
        name_match = _JSON_NAME_RE.match(basename)
        if name_match is None:  # unreachable: the glob guarantees .json
            continue
        stem = name_match.group("stem")
        n = int(name_match.group("n")) if name_match.group("n") is not None else None
        if stem == _METADATA_STEM:
            title = _parse_album_title(stream.read(), member.name)
            if title is not None:
                album_titles[directory] = title
            continue
        sidecar = _parse_sidecar(stream.read(), member.name)
        if sidecar is not None:
            sidecar.n = n
            group = dirs.setdefault(directory, _DirState()).sidecars.setdefault(stem, [])
            # A same-(stem, N) re-listing (multi-part re-export overlap)
            # replaces in place — never a phantom group member.
            for index, existing in enumerate(group):
                if existing.n == n:
                    group[index] = sidecar
                    break
            else:
                group.append(sidecar)
    return dirs, album_titles


def _warn_unclaimed(dirs: dict[str, _DirState]) -> None:
    """A sidecar no media file claimed references a photo the export failed
    to include — that is signal, not noise (real export: zero)."""
    for state in dirs.values():
        for group in state.sidecars.values():
            for sidecar in group:
                if sidecar.claimed_by is None:
                    _logger.warning(
                        "photos: sidecar %r references media the export does not contain — no item",
                        sidecar.member_name,
                    )


@source(
    name="photos",
    detect=_EXPORT_GLOB,
    kinds=(ItemKind.PHOTO,),
    parser_version=1,
)
def parse(archive: Archive, ctx: ParseContext) -> Iterator[PhotoDraft]:
    """Yield PhotoDrafts from every media member, two streaming passes.

    Pass 1 collects the sidecar jsons and album metadata (small — the
    google_chat sidecar posture); pass 2 streams the media bytes, pairs by
    directory + the truncation-aware prefix rule, and yields. Two passes
    because sidecars interleave with (and in multi-part sets may live in
    different parts than) their media; each pass chains all parts. ctx is
    part of the plugin contract but unused: the work is I/O-bound streaming.
    """
    dirs, album_titles = _collect_sidecars(archive)
    first_draft_by_sha: dict[str, PhotoDraft] = {}

    for member, stream in archive.iter_members(f"*{_PRODUCT_SEGMENT}/*"):
        if not _EXPORT_GLOB.matches(member.name):
            continue
        directory, basename = member.name.rsplit("/", 1)
        if basename.endswith(".json"):
            continue  # pass 1 owns jsons
        rel = _rel_after_product(member.name)
        if len(rel) < 2:
            continue  # product-level file (pass 1's rule): never media, no item
        album = album_titles.get(directory) or rel[0]

        state = dirs.get(directory)
        sidecar = _match_sidecar(state, basename) if state is not None else None
        if sidecar is not None:
            if sidecar.claimed_by is not None and sidecar.claimed_by != basename:
                # Truncation collision without a resolving (N) variant: two
                # distinct media prefix-match one sidecar. Best-effort pair,
                # but NEVER silently (review fix cycle 1).
                _logger.warning(
                    "photos: sidecar %r matched by both %r and %r — truncated "
                    "name collision, metadata may be mis-assigned",
                    sidecar.member_name,
                    sidecar.claimed_by,
                    basename,
                )
            else:
                sidecar.claimed_by = basename
        elif state is None or not state.orphan_warned:
            _logger.warning(
                "photos: %r has no sidecar — imported from file facts alone "
                "(further sidecar-less media in this directory are counted silently)",
                member.name,
            )
            if state is None:
                state = dirs.setdefault(directory, _DirState())
            state.orphan_warned = True

        probeable = extension(basename) in PROBE_EXTS
        sha256, size, head = hash_and_head(stream, probeable)

        cached = first_draft_by_sha.get(sha256)
        if cached is not None:
            # Cross-album copy (or a re-listed member): re-yield the first
            # occurrence verbatim so the engine sees an exact duplicate —
            # first album wins meta, sidecar drift cannot cause churn.
            yield cached
            continue

        probe = _probe_image(head, member.name) if probeable else Probe()
        draft = _build_draft(basename, album, sidecar, probe, sha256, size)
        first_draft_by_sha[sha256] = draft
        yield draft

    _warn_unclaimed(dirs)
