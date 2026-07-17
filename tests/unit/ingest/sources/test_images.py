"""Generic image-folder source plugin (#150): EXIF facts, ts/GPS precedence,
byte identity, corrupt-file containment, detection tier.

Hand-crafted archives are built from the photos generator's public byte
helpers (tiny_image) so every test byte is synthetic.
"""

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from potluck.ingest.plugins import ParseContext, discover
from potluck.ingest.readers import open_archive
from potluck.ingest.sources.images import parse
from potluck.models.drafts import PhotoDraft
from potluck.testing.archives import write_archive
from potluck.testing.photos import tiny_image

_ZIP_EPOCH = datetime(1980, 1, 1, tzinfo=UTC)  # write_archive pins zip date_time


def _drafts(
    tmp_path: Path,
    members: dict[str, bytes],
    fmt: Literal["zip", "tgz", "dir"] = "zip",
) -> list[PhotoDraft]:
    dest = tmp_path / ("images_dir" if fmt == "dir" else f"images.{fmt}")
    archive_path = write_archive(dest, members, fmt)
    drafts = list(parse(open_archive(archive_path), ParseContext()))
    return [d for d in drafts if isinstance(d, PhotoDraft)]  # narrows; parse yields only these


def _potluck_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.message for r in caplog.records if r.name.startswith("potluck")]


# ---------------------------------------------------------------------------
# Facts and precedence
# ---------------------------------------------------------------------------


def test_exif_image_full_facts(tmp_path: Path) -> None:
    media = tiny_image(
        make="SynthCam",
        model="SC-1",
        taken="2024:03:01 08:00:00",
        gps=(40.1, -75.1, 30.0),
    )
    [draft] = _drafts(tmp_path, {"Pictures/2024/holiday-snap.jpg": media})
    sha = hashlib.sha256(media).hexdigest()
    assert draft.external_id == f"images:{sha[:16]}"
    assert draft.title == "holiday-snap.jpg"
    assert draft.ts == datetime(2024, 3, 1, 8, 0, tzinfo=UTC)  # EXIF beats member mtime
    assert draft.lat == pytest.approx(40.1)
    assert draft.lon == pytest.approx(-75.1)
    assert draft.gps_alt == pytest.approx(30.0)
    assert draft.width == 32 and draft.height == 24
    assert draft.camera_make == "SynthCam"
    assert draft.camera_model == "SC-1"
    assert draft.mime == "image/jpeg"
    assert draft.size_bytes == len(media)
    assert draft.sha256 == sha
    assert draft.meta == {"type": "photo"}


def test_ts_falls_back_to_member_mtime(tmp_path: Path) -> None:
    """No EXIF DateTimeOriginal -> the container's mtime (zip: the pinned
    1980 epoch, read as UTC)."""
    [draft] = _drafts(tmp_path, {"no-exif.png": tiny_image("PNG")})
    assert draft.ts == _ZIP_EPOCH


def test_ts_none_when_no_exif_and_no_usable_mtime(tmp_path: Path) -> None:
    """tgz members written with mtime=0 (the epoch 'unset' convention) stay
    undated rather than claiming 1970."""
    [draft] = _drafts(tmp_path, {"no-exif.png": tiny_image("PNG")}, fmt="tgz")
    assert draft.ts is None


def test_gps_null_island_reads_as_absent(tmp_path: Path) -> None:
    media = tiny_image(gps=(0.0, 0.0, 5.0))
    [draft] = _drafts(tmp_path, {"zeroed.jpg": media})
    assert draft.lat is None and draft.lon is None and draft.gps_alt is None


def test_probe_format_wins_over_extension(tmp_path: Path) -> None:
    """PNG bytes under a .jpg name: mime comes from the probed format."""
    [draft] = _drafts(tmp_path, {"mislabeled.jpg": tiny_image("PNG")})
    assert draft.mime == "image/png"


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
def test_non_photo_pillow_core_formats_parse(
    tmp_path: Path, fmt: Literal["GIF", "BMP", "TIFF"]
) -> None:
    ext = fmt.lower() if fmt != "TIFF" else "tiff"
    [draft] = _drafts(tmp_path, {f"shot.{ext}": tiny_image(fmt)})
    assert draft.width == 32 and draft.height == 24
    assert draft.meta == {"type": "photo"}


def test_uppercase_extension_detected_and_parsed(tmp_path: Path) -> None:
    """DCIM naming is uppercase on most cameras (IMG_0001.JPG)."""
    media = tiny_image(color=(9, 9, 9))
    [draft] = _drafts(tmp_path, {"DCIM/IMG_0001.JPG": media})
    assert draft.title == "IMG_0001.JPG"
    assert draft.mime == "image/jpeg"


# ---------------------------------------------------------------------------
# Containment and identity
# ---------------------------------------------------------------------------


def test_corrupt_image_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An unreadable file with an image extension is junk in an arbitrary
    folder — skipped with a warning, never a crash, never a blind item."""
    members = {
        "Pictures/corrupt.jpg": b"synthetic bytes that are not an image",
        "Pictures/fine.png": tiny_image("PNG"),
    }
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(tmp_path, members)
    assert [d.title for d in drafts] == ["fine.png"]
    warnings = _potluck_warnings(caplog)
    assert len(warnings) == 1
    assert "corrupt.jpg" in warnings[0] and "skipped" in warnings[0]


def test_duplicate_bytes_reyield_first_draft(tmp_path: Path) -> None:
    """The same photo in two subfolders is ONE logical image: the repeat
    re-yields the first occurrence's draft verbatim (the photos posture), so
    the engine counts an exact duplicate and the first path wins the title."""
    media = tiny_image(color=(1, 2, 3))
    members = {
        "a/first.jpg": media,
        "b/copy-of-first.jpg": media,
    }
    drafts = _drafts(tmp_path, members)
    assert len(drafts) == 2
    assert drafts[0] is drafts[1]
    assert drafts[0].title == "first.jpg"


def test_distinct_bytes_get_distinct_identities(tmp_path: Path) -> None:
    members = {
        "a.jpg": tiny_image(color=(1, 0, 0)),
        "b.jpg": tiny_image(color=(0, 1, 0)),
    }
    drafts = _drafts(tmp_path, members)
    assert len({d.external_id for d in drafts}) == 2


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_registered_as_generic_photo_source() -> None:
    plugin = discover()["images"]
    assert plugin.generic is True
    assert plugin.detect.matches("Pictures/2024/holiday.jpg")
    assert plugin.detect.matches("IMG_0001.JPG")
    assert plugin.detect.matches("deep/nested/shot.webp")
    assert not plugin.detect.matches("archive_browser.html")
    assert not plugin.detect.matches("notes.txt")
    assert not plugin.detect.matches("clip.mp4")
    assert not plugin.detect.matches("photo.heic")  # needs a plugin dep — non-goal
