"""Deterministic synthetic Google Photos Takeout generator.

Ships inside ``potluck.testing`` so tests, committed fixtures, and bench
scenarios share one deterministic source. Same arguments → identical bytes on
one machine and Pillow release; unlike the text generators, image bytes
depend on the installed encoder, so cross-version byte identity is pinned by
the COMMITTED fixture, never by regeneration. Never put real personal data
here — people are fixture names, coordinates live on a fictional
(40.x, -75.x) grid, camera strings are ``SynthCam`` models.

The member set mirrors the real 2025-12 part-12 export (structure verified
against it, shape only): ``Takeout/Google Photos/<album>/`` holding media
files plus one ``<media>.supplemental-metadata.json`` sidecar each — with
Takeout's REAL pathologies reproduced:

- **Stem truncation**: sidecar json stems (name minus ``.json`` and any
  ``(N)``) are capped at exactly 46 chars (measured on all 208 real
  truncated names), cutting ``supplemental-metadata`` mid-word, down to a
  bare ``..json`` ending, and even into the media extension. The bulk
  reproduces the cap mechanically (``stem = (media + suffix)[:46]``) and
  three fixed showcases pin the named depths.
- **The (N) pathology**: ``X.jpg.supplemental-metadata(1).json`` belongs to
  ``X(1).jpg`` (3 real cases). Fixed pair ``synthdup-base.jpg`` /
  ``synthdup-base(1).jpg``.
- **Cross-album byte-duplicate**: ``dup-across-albums.jpg`` appears in both
  albums with identical media AND sidecar bytes (the #149 dedup acceptance
  pair). The real export has zero byte-duplicates, so the fixture carries
  the case.
- **Orphans both ways**: ``orphan-media.jpg`` (media without sidecar, EXIF
  facts only) and ``ghost-photo.jpg.supplemental-metadata.json`` (sidecar
  whose media the export failed to include).
- A named album (``metadata.json`` with title + sharedAlbumComments that
  must never become items), the auto album, a product-root
  ``print-subscriptions.json``, and detection-precision decoys
  (``Google Play Store``, a Drive path containing "Photos",
  ``archive_browser.html``).

Bulk media shapes are modular rules of the index ``i`` (not RNG draws), so
expected parser outcomes have exact closed forms. Per bulk item ``i`` (first
match wins): ``i % 6 == 4`` → mp4 stub (sidecar-only facts); ``i % 9 == 5``
→ PNG; ``i % 11 == 7`` → WEBP; else JPEG with genuine EXIF (Make/Model/
DateTimeOriginal — deliberately one hour BEFORE photoTakenTime so sidecar
precedence is observable). Sidecar geoData is real at ``i % 3 == 0`` and the
0.0/0.0 "no GPS" sentinel otherwise; JPEGs additionally carry EXIF GPS at
``i % 3 == 2`` (the sentinel + EXIF fallback case); people at ``i % 5 ==
1``; description at ``i % 7 == 3``; favorited at ``i % 10 == 6``;
appSource at ``i % 4 == 1``.

Regenerate the golden fixture::

    python -c "
    from pathlib import Path
    from potluck.testing.photos import write_photos_takeout
    write_photos_takeout(Path('tests/fixtures/photos'), 12, seed=7, fmt='dir')
    "
"""

import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import ExifTags, Image

from potluck.testing.archives import split_parts, write_archive
from potluck.testing.generators import WORDS

_BASE_TS = datetime(2024, 3, 1, 8, 0, 0, tzinfo=UTC)

AUTO_ALBUM = "Photos from 2024"
NAMED_ALBUM_DIR = "Synth Album"
NAMED_ALBUM_TITLE = "Synthetic Fixture Album"

_ROOT = "Takeout/Google Photos"
_AUTO = f"{_ROOT}/{AUTO_ALBUM}"
_NAMED = f"{_ROOT}/{NAMED_ALBUM_DIR}"
_SIDECAR_SUFFIX = ".supplemental-metadata"
_STEM_CAP = 46  # measured: every real truncated stem is exactly 46 chars

_PEOPLE = ("Ada Example", "Bo Sample", "Cy Test")

# Fixed specials contribute this many UNIQUE items on top of the bulk
# ``count``: cross-album dup pair = 1 (two members, one item), (N) pair = 2,
# orphan = 1, ts-precedence pair = 2, truncation showcases = 3,
# GPS video = 1, named-album photos = 2.
_SPECIAL_ITEMS = 12
_SPECIAL_VIDEOS = 1
_SPECIAL_COORDS = 4  # dup pair, synthdup base, orphan (EXIF), GPS video

_DUP_EPOCH = int(datetime(2024, 3, 2, 9, 0, tzinfo=UTC).timestamp())


def media_ts(i: int) -> datetime:
    """The capture instant of bulk item *i*: 2 hours apart with jitter."""
    return _BASE_TS + timedelta(hours=2 * i, seconds=(i * 17) % 60)


def _bulk_kind(i: int) -> str:
    if i % 6 == 4:
        return "mp4"
    if i % 9 == 5:
        return "png"
    if i % 11 == 7:
        return "webp"
    return "jpg"


def expected_item_count(count: int) -> int:
    """Unique items the parser yields for a *count*-bulk archive."""
    return count + _SPECIAL_ITEMS


def expected_video_count(count: int) -> int:
    """Items with meta.type == video."""
    return sum(1 for i in range(count) if _bulk_kind(i) == "mp4") + _SPECIAL_VIDEOS


def expected_coordinate_count(count: int) -> int:
    """Items carrying lat/lon: sidecar geoData at ``i % 3 == 0``, EXIF GPS
    fallback on JPEGs at ``i % 3 == 2``, plus the fixed specials."""
    bulk = sum(1 for i in range(count) if i % 3 == 0 or (i % 3 == 2 and _bulk_kind(i) == "jpg"))
    return bulk + _SPECIAL_COORDS


# ---------------------------------------------------------------------------
# Byte builders (public: parser tests hand-craft archives from these)
# ---------------------------------------------------------------------------


def _deg_to_dms(value: float) -> tuple[float, float, float]:
    degrees = int(value)
    rem = (value - degrees) * 60
    minutes = int(rem)
    seconds = round((rem - minutes) * 60, 4)
    return (float(degrees), float(minutes), seconds)


def tiny_image(
    fmt: Literal["JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF"] = "JPEG",
    *,
    size: tuple[int, int] = (32, 24),
    color: tuple[int, int, int] = (200, 30, 40),
    make: str | None = None,
    model: str | None = None,
    taken: str | None = None,
    offset: str | None = None,
    gps: tuple[float, float, float] | None = None,
) -> bytes:
    """A tiny valid image with genuine EXIF written through Pillow.

    ``taken`` is the EXIF rendering (``YYYY:MM:DD HH:MM:SS``), ``offset`` the
    optional OffsetTimeOriginal (``+05:30``), ``gps`` a (lat, lon, alt)
    triple encoded as real GPS IFD rationals with N/S/E/W refs.

    Whole-IFD dict assignment is deliberate: per-IFD ``get_ifd()`` mutation
    does not survive ``save(exif=...)`` on the Pillow 10.4 floor.
    """
    img = Image.new("RGB", size, color)
    exif = Image.Exif()
    if make is not None:
        exif[ExifTags.Base.Make] = make
    if model is not None:
        exif[ExifTags.Base.Model] = model
    if taken is not None:
        exif_ifd: dict[int, str] = {ExifTags.Base.DateTimeOriginal: taken}
        if offset is not None:
            exif_ifd[ExifTags.Base.OffsetTimeOriginal] = offset
        exif[ExifTags.IFD.Exif] = exif_ifd
    if gps is not None:
        lat, lon, alt = gps
        exif[ExifTags.IFD.GPSInfo] = {
            ExifTags.GPS.GPSLatitudeRef: "N" if lat >= 0 else "S",
            ExifTags.GPS.GPSLatitude: _deg_to_dms(abs(lat)),
            ExifTags.GPS.GPSLongitudeRef: "E" if lon >= 0 else "W",
            ExifTags.GPS.GPSLongitude: _deg_to_dms(abs(lon)),
            ExifTags.GPS.GPSAltitudeRef: b"\x00" if alt >= 0 else b"\x01",
            ExifTags.GPS.GPSAltitude: abs(alt),
        }
    buf = BytesIO()
    img.save(buf, format=fmt, exif=exif)
    return buf.getvalue()


def stub_mp4(variant: int = 0) -> bytes:
    """A few dozen bytes shaped like an mp4 header — never probed, only
    hashed; *variant* keeps multiple stubs byte-distinct."""
    return (
        b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
        + b"\x00\x00\x00\x0cfree"
        + variant.to_bytes(4, "big")
        + b"\x00" * 32
    )


def _time_block(epoch: int) -> dict[str, str]:
    dt = datetime.fromtimestamp(epoch, tz=UTC)
    # The parser never reads "formatted"; a deterministic plain rendering
    # keeps the key present like the real export without locale games.
    return {"timestamp": str(epoch), "formatted": dt.strftime("%b %d, %Y, %I:%M:%S %p UTC")}


def _geo_block(geo: tuple[float, float, float] | None) -> dict[str, float]:
    lat, lon, alt = geo if geo is not None else (0.0, 0.0, 0.0)
    return {
        "latitude": lat,
        "longitude": lon,
        "altitude": alt,
        "latitudeSpan": 0.0,
        "longitudeSpan": 0.0,
    }


def sidecar_json(
    title: str,
    *,
    description: str = "",
    taken_epoch: int | None = None,
    creation_epoch: int | None = None,
    geo: tuple[float, float, float] | None = None,
    geo_exif: tuple[float, float, float] | None = None,
    people: tuple[str, ...] = (),
    favorited: bool | None = None,
    url: str = "https://photos.google.com/photo/synthetic",
    device_folder: str | None = "Camera",
    app_source: str | None = None,
) -> bytes:
    """One supplemental-metadata sidecar, key names and order as exported.

    Like the real export, ``geoData`` is ALWAYS present — the 0.0/0.0 "no
    GPS" sentinel when *geo* is None; ``photoTakenTime`` is present only
    when *taken_epoch* is given (the ts-precedence fallback case);
    ``creationTime`` is always present (default: taken + 1 day).
    """
    if creation_epoch is None:
        creation_epoch = (taken_epoch if taken_epoch is not None else 1709280000) + 86400
    doc: dict[str, object] = {
        "title": title,
        "description": description,
        "imageViews": "1",
        "creationTime": _time_block(creation_epoch),
    }
    if taken_epoch is not None:
        doc["photoTakenTime"] = _time_block(taken_epoch)
    doc["geoData"] = _geo_block(geo)
    if geo_exif is not None:
        doc["geoDataExif"] = _geo_block(geo_exif)
    if people:
        doc["people"] = [{"name": name} for name in people]
    doc["url"] = url
    origin: dict[str, object] = {"deviceType": "ANDROID_PHONE"}
    if device_folder is not None:
        origin = {"deviceFolder": {"localFolderName": device_folder}, "deviceType": "ANDROID_PHONE"}
    doc["googlePhotosOrigin"] = {"mobileUpload": origin}
    if app_source is not None:
        doc["appSource"] = {"androidPackageName": app_source}
    if favorited is not None:
        doc["favorited"] = favorited
    return json.dumps(doc, ensure_ascii=False, indent=2).encode()


# ---------------------------------------------------------------------------
# Member set
# ---------------------------------------------------------------------------


def _sidecar_name(media_name: str, n: int | None = None) -> str:
    """The exported sidecar filename: stem capped at 46 chars (Takeout's
    rule, measured), optional ``(N)`` between stem and ``.json``."""
    stem = (media_name + _SIDECAR_SUFFIX)[:_STEM_CAP]
    return f"{stem}({n}).json" if n is not None else f"{stem}.json"


def _words(salt: int, i: int, k: int) -> str:
    return " ".join(WORDS[(salt + i * 7 + j * 3) % len(WORDS)] for j in range(k))


def _bulk_members(count: int, salt: int) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    for i in range(count):
        kind = _bulk_kind(i)
        name = f"synthetic-photo-{i:04d}-" + "p" * (i % 29) + f".{kind}"
        ts = media_ts(i)
        color = ((salt + i * 37) % 256, (salt // 3 + i * 59) % 256, (salt // 7 + i * 83) % 256)
        if kind == "mp4":
            media = stub_mp4(i)
        else:
            fmt: Literal["JPEG", "PNG", "WEBP"] = (
                "JPEG" if kind == "jpg" else "PNG" if kind == "png" else "WEBP"
            )
            exif_gps = None
            make = model = taken = None
            if kind == "jpg":
                make, model = "SynthCam", f"SC-{i % 4}"
                taken = (ts - timedelta(hours=1)).strftime("%Y:%m:%d %H:%M:%S")
                if i % 3 == 2:
                    exif_gps = (40.5 + i * 0.001, -74.5 - i * 0.001, 5.0 + i)
            media = tiny_image(
                fmt,
                size=(32 + (i % 5) * 8, 24 + (i % 3) * 8),
                color=color,
                make=make,
                model=model,
                taken=taken,
                gps=exif_gps,
            )
        members[f"{_AUTO}/{name}"] = media
        members[f"{_AUTO}/{_sidecar_name(name)}"] = sidecar_json(
            name,
            description=_words(salt, i, 4) if i % 7 == 3 else "",
            taken_epoch=int(ts.timestamp()),
            geo=(40.01 + i * 0.003, -75.02 - i * 0.003, 10.0 + i) if i % 3 == 0 else None,
            people=(_PEOPLE[i % 3],) if i % 5 == 1 else (),
            favorited=True if i % 10 == 6 else None,
            url=f"https://photos.google.com/photo/synthetic-{i:04d}",
            app_source="test.synthetic.app" if i % 4 == 1 else None,
        )
    return members


def _special_members() -> dict[str, bytes]:
    members: dict[str, bytes] = {}

    # Cross-album byte-duplicate: identical media AND sidecar bytes twice.
    dup_media = tiny_image(
        color=(10, 120, 200), make="SynthCam", model="SC-DUP", taken="2024:03:02 08:00:00"
    )
    dup_sidecar = sidecar_json(
        "dup-across-albums.jpg", taken_epoch=_DUP_EPOCH, geo=(40.2, -75.2, 33.0)
    )
    for album in (_AUTO, _NAMED):
        members[f"{album}/dup-across-albums.jpg"] = dup_media
        members[f"{album}/{_sidecar_name('dup-across-albums.jpg')}"] = dup_sidecar

    # The (N) pathology pair.
    members[f"{_AUTO}/synthdup-base.jpg"] = tiny_image(color=(60, 60, 10))
    members[f"{_AUTO}/synthdup-base(1).jpg"] = tiny_image(color=(10, 60, 60))
    members[f"{_AUTO}/{_sidecar_name('synthdup-base.jpg')}"] = sidecar_json(
        "synthdup-base.jpg", taken_epoch=_DUP_EPOCH + 100, geo=(40.3, -75.3, 12.0)
    )
    members[f"{_AUTO}/{_sidecar_name('synthdup-base.jpg', n=1)}"] = sidecar_json(
        "synthdup-base.jpg", taken_epoch=_DUP_EPOCH + 200
    )

    # Media without sidecar (EXIF facts only) / sidecar without media.
    members[f"{_AUTO}/orphan-media.jpg"] = tiny_image(
        color=(120, 10, 10),
        make="SynthCam",
        model="SC-9",
        taken="2024:03:05 06:30:00",
        offset="+05:30",
        gps=(40.25, -75.25, 21.0),
    )
    members[f"{_AUTO}/{_sidecar_name('ghost-photo.jpg')}"] = sidecar_json(
        "ghost-photo.jpg", taken_epoch=_DUP_EPOCH + 300
    )

    # ts-precedence pair: no photoTakenTime → EXIF DateTimeOriginal, then
    # neither → creationTime.
    members[f"{_AUTO}/no-taken-time.jpg"] = tiny_image(
        color=(20, 90, 20), taken="2024:03:05 06:30:00"
    )
    members[f"{_AUTO}/{_sidecar_name('no-taken-time.jpg')}"] = sidecar_json(
        "no-taken-time.jpg", creation_epoch=_DUP_EPOCH + 400
    )
    members[f"{_AUTO}/no-taken-no-exif.png"] = tiny_image("PNG", color=(90, 20, 90))
    members[f"{_AUTO}/{_sidecar_name('no-taken-no-exif.png')}"] = sidecar_json(
        "no-taken-no-exif.png", creation_epoch=_DUP_EPOCH + 500
    )

    # Truncation showcases at the named depths (stem lengths verified in
    # tests): mid-word cut, the '..json' ending, extension-eating.
    for offset_epoch, name in (
        (600, "trunc-midword-01234567890a.jpg"),  # len 30 → stem ends '.supplemental-me'
        (700, "trunc-dotonly-" + "d" * 27 + ".jpg"),  # len 45 → stem ends '.'
        (800, "trunc-exteaten-" + "e" * 29 + ".jpg"),  # len 48 → stem ends '.j'
    ):
        members[f"{_AUTO}/{name}"] = tiny_image(color=(offset_epoch % 256, 40, 40))
        members[f"{_AUTO}/{_sidecar_name(name)}"] = sidecar_json(
            name, taken_epoch=_DUP_EPOCH + offset_epoch
        )

    # A video with sidecar GPS (sidecar-only facts).
    members[f"{_AUTO}/video-with-gps.mp4"] = stub_mp4(999)
    members[f"{_AUTO}/{_sidecar_name('video-with-gps.mp4')}"] = sidecar_json(
        "video-with-gps.mp4", taken_epoch=_DUP_EPOCH + 900, geo=(40.4, -75.4, 21.0)
    )

    # The named album: metadata.json (title + comments that never import).
    members[f"{_NAMED}/metadata.json"] = json.dumps(
        {
            "title": NAMED_ALBUM_TITLE,
            "sharedAlbumComments": [
                {
                    "text": "synthetic shared-album comment, never an item",
                    "creationTime": _time_block(_DUP_EPOCH),
                    "contentOwnerName": "Bo Sample",
                }
            ],
        },
        ensure_ascii=False,
        indent=2,
    ).encode()
    members[f"{_NAMED}/album-photo-01.jpg"] = tiny_image(
        color=(200, 200, 10), make="SynthCam", model="SC-A"
    )
    members[f"{_NAMED}/{_sidecar_name('album-photo-01.jpg')}"] = sidecar_json(
        "album-photo-01.jpg",
        taken_epoch=_DUP_EPOCH + 1000,
        people=("Ada Example",),
        favorited=True,
    )
    members[f"{_NAMED}/album-photo-02.jpg"] = tiny_image(color=(10, 200, 200))
    members[f"{_NAMED}/{_sidecar_name('album-photo-02.jpg')}"] = sidecar_json(
        "album-photo-02.jpg", taken_epoch=_DUP_EPOCH + 1100, description="synthetic caption"
    )

    # Product-root json (skipped silently) + detection-precision decoys.
    members[f"{_ROOT}/print-subscriptions.json"] = b'{"printSubscriptions": []}'
    members["Takeout/Google Play Store/Library.json"] = b'{"library": []}'
    members["Takeout/Drive/My Photos/vacation-snap.jpg"] = tiny_image(color=(1, 1, 1))
    members["Takeout/archive_browser.html"] = b"<html>synthetic decoy</html>"
    return members


def photos_members(count: int, seed: int = 42) -> dict[str, bytes]:
    """The member set of one synthetic Takeout ({posix_name: content})."""
    salt = seed * 1009
    members = _bulk_members(count, salt)
    members.update(_special_members())
    return members


def write_photos_takeout(
    dest_dir: Path,
    count: int,
    seed: int = 42,
    *,
    fmt: Literal["zip", "tgz", "dir"] = "zip",
) -> Path:
    """Materialise a synthetic Google Photos Takeout archive in *dest_dir*.

    Returns the archive path (or the directory root for ``fmt="dir"``).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    members = photos_members(count, seed)
    if fmt == "dir":
        dest = dest_dir / "photos-synth-001"
        write_archive(dest, members, "dir")
        return dest
    ext = "zip" if fmt == "zip" else "tgz"
    dest = dest_dir / f"photos-synth-001.{ext}"
    write_archive(dest, members, fmt)
    return dest


def write_photos_takeout_parts(
    dest_dir: Path, count: int, seed: int = 42, *, parts: int = 2
) -> list[Path]:
    """A multi-part tgz set (``photos-synth-001.tgz`` …), round-robin split
    over sorted names — adjacent names land in different parts, and a media
    file sorts adjacent to its own sidecar, so sidecar/media pairs are
    guaranteed to straddle parts (the #149 cross-part acceptance case).

    Returns the part paths in order; opening any one of them opens the set.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, part_members in enumerate(split_parts(photos_members(count, seed), parts), start=1):
        path = dest_dir / f"photos-synth-{index:03d}.tgz"
        write_archive(path, part_members, "tgz")
        paths.append(path)
    return paths
