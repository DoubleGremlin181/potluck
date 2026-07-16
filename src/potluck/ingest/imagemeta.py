"""Shared image metadata probing: streaming hash + Pillow header/EXIF facts.

Extracted from the photos source (#149) for reuse by the generic image-folder
source (#150) — one probe implementation, two containment policies (photos
imports a malformed image from byte facts alone; images skips it), so
:func:`probe_image` RAISES on unreadable input and each caller owns its own
``except``/warning. Pillow's real failure surface is broad
(UnidentifiedImageError, OSError "Truncated File Read", ValueError,
SyntaxError, ZeroDivisionError on 0-denominator rationals), which is why the
call sites use a documented blanket ``except Exception``.

The probe never decodes pixels: ``.size`` / ``.getexif`` are header reads,
and headers + EXIF live at the front of every Pillow-core format — hence the
HEAD_CAP buffering strategy in :func:`hash_and_head`.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from io import BytesIO
from typing import IO, Final

from PIL import ExifTags, Image

_CHUNK: Final = 1 << 20
# Probe head cap: JPEG/PNG headers + EXIF live at the front; 32 MiB covers
# every real photo (2025-12 part-12 mean ~4 MB) while keeping memory flat.
HEAD_CAP: Final = 32 * 1024 * 1024
# Extensions Pillow decodes without plugins; everything else (mp4/mov/heic/…)
# is hashed without buffering and never probed — silent by design.
PROBE_EXTS: Final = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"})

# EXIF DateTimeOriginal rendering; the offset shape of OffsetTimeOriginal.
_EXIF_DT_RE: Final = re.compile(r"^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})")
_EXIF_OFFSET_RE: Final = re.compile(r"^([+-])(\d{2}):(\d{2})$")


@dataclass(slots=True)
class Probe:
    """What Pillow extracted from a media head (all-None when not probed)."""

    width: int | None = None
    height: int | None = None
    mime: str | None = None
    make: str | None = None
    model: str | None = None
    taken: datetime | None = None
    gps: tuple[float, float, float | None] | None = None


def _exif_str(value: object) -> str | None:
    """EXIF string values arrive NUL/space-padded; bytes are decoded first."""
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")
    if not isinstance(value, str):
        return None
    return value.strip("\x00 \t") or None


def _parse_exif_datetime(raw: object, raw_offset: object) -> datetime | None:
    """``YYYY:MM:DD HH:MM:SS`` (+ optional OffsetTimeOriginal) → aware
    instant. A naive value reads as UTC — the whatsapp/gmail unknown-zone
    policy; all-zero placeholders and impossible dates return None."""
    text = _exif_str(raw)
    if text is None:
        return None
    m = _EXIF_DT_RE.match(text)
    if m is None:
        return None
    tz = UTC
    offset_text = _exif_str(raw_offset)
    if offset_text is not None:
        om = _EXIF_OFFSET_RE.match(offset_text)
        if om is not None:
            sign = 1 if om.group(1) == "+" else -1
            tz = timezone(sign * timedelta(hours=int(om.group(2)), minutes=int(om.group(3))))
    year, month, day, hour, minute, second = (int(g) for g in m.groups())
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=tz)
    except ValueError:  # impossible dates, including the 0000:00:00 placeholder
        return None


def _dms_to_degrees(values: object, ref: object) -> float | None:
    """GPS DMS rationals + hemisphere ref → signed decimal degrees, or None
    (0-denominator rationals and foreign shapes are rejected, never 0.0)."""
    if not isinstance(values, (tuple, list)) or not 1 <= len(values) <= 3:
        return None
    try:
        parts = [float(v) for v in values]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    degrees = sum(part / (60.0**power) for power, part in enumerate(parts))
    ref_text = _exif_str(ref)
    if ref_text in ("S", "W"):
        degrees = -degrees
    return degrees


def _parse_exif_gps(gps_ifd: dict[int, object]) -> tuple[float, float, float | None] | None:
    """The GPS IFD → (lat, lon, alt), or None. Null Island (0,0) is the same
    junk sentinel as the Takeout sidecar's and reads as absent."""
    lat = _dms_to_degrees(
        gps_ifd.get(ExifTags.GPS.GPSLatitude), gps_ifd.get(ExifTags.GPS.GPSLatitudeRef)
    )
    lon = _dms_to_degrees(
        gps_ifd.get(ExifTags.GPS.GPSLongitude), gps_ifd.get(ExifTags.GPS.GPSLongitudeRef)
    )
    if lat is None or lon is None:
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    alt: float | None = None
    raw_alt = gps_ifd.get(ExifTags.GPS.GPSAltitude)
    if raw_alt is not None:
        try:
            alt = float(raw_alt)  # type: ignore[arg-type]
        except (TypeError, ValueError, ZeroDivisionError):
            alt = None
        else:
            if gps_ifd.get(ExifTags.GPS.GPSAltitudeRef) in (1, b"\x01"):
                alt = -alt
    return lat, lon, alt


def probe_image(head: bytes) -> Probe:
    """Pillow over the buffered head: dimensions, MIME, camera, capture
    time, GPS. Never decodes pixels (.size/.getexif read headers only).

    Raises whatever Pillow raises on unreadable input (module docstring) —
    the caller owns containment, because the right degradation is
    source-policy (photos: import from byte facts; images: skip).
    """
    probe = Probe()
    with Image.open(BytesIO(head)) as image:
        probe.width, probe.height = image.size
        probe.mime = Image.MIME.get(image.format or "")
        exif = image.getexif()
        probe.make = _exif_str(exif.get(ExifTags.Base.Make))
        probe.model = _exif_str(exif.get(ExifTags.Base.Model))
        exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
        probe.taken = _parse_exif_datetime(
            exif_ifd.get(ExifTags.Base.DateTimeOriginal),
            exif_ifd.get(ExifTags.Base.OffsetTimeOriginal),
        )
        probe.gps = _parse_exif_gps(dict(exif.get_ifd(ExifTags.IFD.GPSInfo)))
    return probe


def hash_and_head(stream: IO[bytes], probeable: bool) -> tuple[str, int, bytes]:
    """One streaming pass: sha256 + size over every byte, buffering only the
    first HEAD_CAP bytes of probeable images (b"" otherwise)."""
    digest = hashlib.sha256()
    size = 0
    head = bytearray()
    while chunk := stream.read(_CHUNK):
        digest.update(chunk)
        size += len(chunk)
        if probeable and len(head) < HEAD_CAP:
            head.extend(chunk[: HEAD_CAP - len(head)])
    return digest.hexdigest(), size, bytes(head)


def extension(basename: str) -> str:
    """The lowercased final extension including the dot, '' when none."""
    dot = basename.rfind(".")
    return basename[dot:].lower() if dot >= 0 else ""
