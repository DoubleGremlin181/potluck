"""Determinism and shape guarantees of the synthetic Google Photos generator."""

from io import BytesIO
from pathlib import Path

from PIL import ExifTags, Image

from potluck.testing.photos import (
    AUTO_ALBUM,
    NAMED_ALBUM_DIR,
    expected_coordinate_count,
    expected_item_count,
    expected_video_count,
    photos_members,
    sidecar_json,
    tiny_image,
    write_photos_takeout,
    write_photos_takeout_parts,
)

_SIDECAR_SUFFIX = ".supplemental-metadata"
_AUTO = f"Takeout/Google Photos/{AUTO_ALBUM}"
_NAMED = f"Takeout/Google Photos/{NAMED_ALBUM_DIR}"


def test_same_args_same_bytes() -> None:
    assert photos_members(20, seed=7) == photos_members(20, seed=7)


def test_seed_changes_content() -> None:
    a = photos_members(20, seed=7)
    b = photos_members(20, seed=8)
    assert a.keys() == b.keys()  # layout is stable; content varies
    assert a != b


def test_member_set_covers_the_acceptance_shapes() -> None:
    members = photos_members(12, seed=7)
    names = set(members)

    # Album metadata + named-album content.
    assert f"{_NAMED}/metadata.json" in names
    assert f"{_NAMED}/album-photo-01.jpg" in names

    # Cross-album byte-duplicate pair: identical media bytes AND identical
    # sidecar bytes in both albums.
    assert members[f"{_AUTO}/dup-across-albums.jpg"] == members[f"{_NAMED}/dup-across-albums.jpg"]
    assert (
        members[f"{_AUTO}/dup-across-albums.jpg{_SIDECAR_SUFFIX}.json"]
        == members[f"{_NAMED}/dup-across-albums.jpg{_SIDECAR_SUFFIX}.json"]
    )

    # The (N) pathology: X.jpg + X(1).jpg with sidecars X.jpg.supplemental-
    # metadata.json and X.jpg.supplemental-metadata(1).json.
    assert f"{_AUTO}/synthdup-base.jpg" in names
    assert f"{_AUTO}/synthdup-base(1).jpg" in names
    assert f"{_AUTO}/synthdup-base.jpg{_SIDECAR_SUFFIX}.json" in names
    assert f"{_AUTO}/synthdup-base.jpg{_SIDECAR_SUFFIX}(1).json" in names
    assert members[f"{_AUTO}/synthdup-base.jpg"] != members[f"{_AUTO}/synthdup-base(1).jpg"]

    # Media without sidecar / sidecar without media.
    assert f"{_AUTO}/orphan-media.jpg" in names
    assert f"{_AUTO}/orphan-media.jpg{_SIDECAR_SUFFIX}.json" not in names
    assert f"{_AUTO}/ghost-photo.jpg{_SIDECAR_SUFFIX}.json" in names
    assert f"{_AUTO}/ghost-photo.jpg" not in names

    # Detection-precision decoys.
    assert "Takeout/Google Play Store/Library.json" in names
    assert "Takeout/Drive/My Photos/vacation-snap.jpg" in names
    assert "Takeout/Google Photos/print-subscriptions.json" in names


def test_truncated_sidecar_names_cover_the_real_depths() -> None:
    """Takeout truncates the json stem to exactly 46 chars (measured on all
    208 real truncated names). The generator must cover: mid-word cuts,
    the ``..json`` ending, and cuts that eat into the media extension."""
    members = photos_members(30, seed=7)
    sidecar_stems = [
        name.rsplit("/", 1)[-1][: -len(".json")]
        for name in members
        if name.endswith(".json") and name.rsplit("/", 1)[-1] != "metadata.json"
    ]
    stems = [s[: s.rfind("(")] if s.endswith(")") and "(" in s else s for s in sidecar_stems]

    assert all(len(s) <= 46 for s in stems)
    assert any(len(s) == 46 for s in stems)  # the cap is exercised
    assert any(s.endswith(".") for s in stems)  # the '..json' ending
    assert any(s.endswith(".supplemental-me") for s in stems)  # mid-word cut
    assert any(s.endswith(".j") for s in stems)  # cut ate the media extension
    assert any(s.endswith(_SIDECAR_SUFFIX) for s in stems)  # canonical names too


def test_tiny_image_exif_round_trips() -> None:
    data = tiny_image(
        make="SynthCam",
        model="SC-42",
        taken="2024:03:05 06:30:00",
        offset="+05:30",
        gps=(40.052, -75.161, 12.5),
    )
    with Image.open(BytesIO(data)) as im:
        assert im.format == "JPEG"
        exif = im.getexif()
        assert exif.get(ExifTags.Base.Make) == "SynthCam"
        assert exif.get(ExifTags.Base.Model) == "SC-42"
        exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
        assert exif_ifd.get(ExifTags.Base.DateTimeOriginal) == "2024:03:05 06:30:00"
        assert exif_ifd.get(ExifTags.Base.OffsetTimeOriginal) == "+05:30"
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        assert gps.get(ExifTags.GPS.GPSLatitudeRef) == "N"
        assert gps.get(ExifTags.GPS.GPSLongitudeRef) == "W"
        lat = [float(v) for v in gps[ExifTags.GPS.GPSLatitude]]
        assert lat[0] == 40.0
        alt = float(gps[ExifTags.GPS.GPSAltitude])
        assert alt == 12.5


def test_sidecar_json_zero_geo_sentinel_by_default() -> None:
    """Like the real export, geoData is ALWAYS present — 0.0/0.0 when the
    photo has no location (the sentinel the parser must never emit)."""
    import json

    doc = json.loads(sidecar_json("x.jpg", creation_epoch=1700000000))
    assert doc["geoData"]["latitude"] == 0.0
    assert doc["geoData"]["longitude"] == 0.0
    assert doc["title"] == "x.jpg"
    assert doc["creationTime"]["timestamp"] == "1700000000"


def test_closed_forms_scale_with_count() -> None:
    for count in (12, 30):
        specials = expected_item_count(count) - count
        assert specials == expected_item_count(0)  # fixed specials, count-independent
        assert expected_video_count(count) >= 1
        assert 0 < expected_coordinate_count(count) <= expected_item_count(count)


def test_write_dir_and_archive_formats(tmp_path: Path) -> None:
    root = write_photos_takeout(tmp_path / "d", 6, seed=7, fmt="dir")
    assert (root / f"{_NAMED}/metadata.json").is_file()

    zip_path = write_photos_takeout(tmp_path / "z", 6, seed=7, fmt="zip")
    assert zip_path.suffix == ".zip" and zip_path.is_file()


def test_write_multi_part_splits_pairs_across_parts(tmp_path: Path) -> None:
    """Two-part sets must exercise the sidecar-in-part-1 / media-in-part-2
    split (round-robin over sorted names sends adjacent names to different
    parts, and a media file sorts adjacent to its own sidecar)."""
    parts = write_photos_takeout_parts(tmp_path, 6, seed=7, parts=2)
    assert [p.name for p in parts] == ["photos-synth-001.tgz", "photos-synth-002.tgz"]

    import tarfile

    names_by_part: list[set[str]] = []
    for p in parts:
        with tarfile.open(p) as tf:
            names_by_part.append({m.name for m in tf.getmembers() if m.isfile()})
    all_names = names_by_part[0] | names_by_part[1]
    assert not (names_by_part[0] & names_by_part[1])

    split_pairs = 0
    for name in all_names:
        if not name.endswith(".json"):
            sidecar = f"{name}{_SIDECAR_SUFFIX}.json"
            if sidecar in all_names:
                in_same = any(name in ns and sidecar in ns for ns in names_by_part)
                split_pairs += 0 if in_same else 1
    assert split_pairs > 0
