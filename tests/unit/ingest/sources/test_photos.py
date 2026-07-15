"""Google Photos source plugin (#149): pairing, precedence, identity, containment.

Hand-crafted archives are built from the generator's public byte helpers
(tiny_image / sidecar_json) so every test byte is synthetic; archive-level
tests reuse the full generator. Real-shape claims (the 46-char stem cap, the
(N) transfer pathology, the 0.0/0.0 sentinel) were verified against the real
part-12 export — shape only, never content.
"""

import hashlib
import logging
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from potluck.ingest.plugins import ParseContext, discover
from potluck.ingest.readers import open_archive
from potluck.ingest.sources.photos import parse
from potluck.models.drafts import PhotoDraft
from potluck.testing.archives import write_archive
from potluck.testing.photos import (
    expected_coordinate_count,
    expected_item_count,
    expected_video_count,
    sidecar_json,
    tiny_image,
    write_photos_takeout,
    write_photos_takeout_parts,
)

_ALBUM = "Takeout/Google Photos/Photos from 2024"
_NAMED = "Takeout/Google Photos/Synth Album"
_EPOCH = 1709280000  # 2024-03-01T08:00:00Z
_TAKEN = datetime.fromtimestamp(_EPOCH, tz=UTC)


def _drafts(tmp_path: Path, members: dict[str, bytes]) -> list[PhotoDraft]:
    archive_path = write_archive(tmp_path / "photos.zip", members, "zip")
    drafts = list(parse(open_archive(archive_path), ParseContext()))
    return [d for d in drafts if isinstance(d, PhotoDraft)]  # narrows; parse yields only these


def _potluck_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.message for r in caplog.records if r.name.startswith("potluck")]


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------


def test_canonical_sidecar_pairs_everything(tmp_path: Path) -> None:
    media = tiny_image(make="SynthCam", model="SC-1", taken="2024:01:01 00:00:00")
    members = {
        f"{_ALBUM}/holiday-snap.jpg": media,
        f"{_ALBUM}/holiday-snap.jpg.supplemental-metadata.json": sidecar_json(
            "holiday-snap.jpg",
            taken_epoch=_EPOCH,
            creation_epoch=_EPOCH + 3600,
            geo=(40.1, -75.1, 30.0),
        ),
    }
    [draft] = _drafts(tmp_path, members)
    assert draft.external_id == f"photos:{hashlib.sha256(media).hexdigest()[:16]}"
    assert draft.title == "holiday-snap.jpg"
    assert draft.ts == _TAKEN
    assert draft.lat == 40.1
    assert draft.lon == -75.1
    assert draft.gps_alt == 30.0
    assert draft.width == 32 and draft.height == 24
    assert draft.camera_make == "SynthCam"
    assert draft.camera_model == "SC-1"
    assert draft.mime == "image/jpeg"
    assert draft.size_bytes == len(media)
    assert draft.sha256 == hashlib.sha256(media).hexdigest()
    assert draft.meta["type"] == "photo"
    assert draft.meta["album"] == "Photos from 2024"


@pytest.mark.parametrize(
    "stem",
    [
        "trunc-case.jpg.supplemental-metadata",  # canonical, untruncated
        "trunc-case.jpg.supplemental-metad",  # mid-word cut
        "trunc-case.jpg.suppl",
        "trunc-case.jpg.su",
        "trunc-case.jpg.",  # the '..json' ending
        "trunc-case.jpg",  # exactly the media name
        "trunc-case.j",  # cut ate the media extension
    ],
)
def test_truncated_sidecar_names_pair(tmp_path: Path, stem: str) -> None:
    """The pairing rule: the json stem must be a PREFIX of
    ``<media>.supplemental-metadata`` in the same directory — verified
    against all 208 real truncated names (46-char stem cap)."""
    members = {
        f"{_ALBUM}/trunc-case.jpg": tiny_image(),
        f"{_ALBUM}/{stem}.json": sidecar_json("original-name.jpg", taken_epoch=_EPOCH),
    }
    [draft] = _drafts(tmp_path, members)
    assert draft.title == "original-name.jpg"  # sidecar title proves the pairing
    assert draft.ts == _TAKEN


def test_n_variant_pairs_with_n_suffixed_media(tmp_path: Path) -> None:
    """The Takeout (N) pathology (real: 3 cases, all resolving this way):
    ``X.jpg.supplemental-metadata(1).json`` belongs to ``X(1).jpg``, not to
    ``X.jpg`` — the (N) transfers to just before the media extension."""
    members = {
        f"{_ALBUM}/synthdup-base.jpg": tiny_image(color=(10, 20, 30)),
        f"{_ALBUM}/synthdup-base(1).jpg": tiny_image(color=(40, 50, 60)),
        f"{_ALBUM}/synthdup-base.jpg.supplemental-metadata.json": sidecar_json(
            "synthdup-base.jpg", description="base copy", taken_epoch=_EPOCH
        ),
        f"{_ALBUM}/synthdup-base.jpg.supplemental-metadata(1).json": sidecar_json(
            "synthdup-base.jpg", description="n copy", taken_epoch=_EPOCH
        ),
    }
    drafts = {d.title: d for d in _drafts(tmp_path, members)}
    assert len(drafts) == 1  # both sidecars carry the same original title
    by_text = {d.text: d for d in _drafts(tmp_path, members)}
    assert set(by_text) == {"base copy", "n copy"}


def test_unclaimed_sidecar_warns_and_yields_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    members = {
        f"{_ALBUM}/ghost-photo.jpg.supplemental-metadata.json": sidecar_json(
            "ghost-photo.jpg", taken_epoch=_EPOCH
        ),
    }
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(tmp_path, members)
    assert drafts == []
    warnings = _potluck_warnings(caplog)
    assert len(warnings) == 1
    assert "ghost-photo" in warnings[0]


def test_media_without_sidecar_imports_from_file_facts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A sidecar-less media file still imports (EXIF facts only), with one
    warning latched per directory."""
    members = {
        f"{_ALBUM}/orphan-one.jpg": tiny_image(
            make="SynthCam",
            model="SC-9",
            taken="2024:03:05 06:30:00",
            offset="+05:30",
            gps=(40.2, -75.2, 21.0),
        ),
        f"{_ALBUM}/orphan-two.jpg": tiny_image(color=(9, 9, 9)),
    }
    with caplog.at_level(logging.WARNING):
        drafts = sorted(_drafts(tmp_path, members), key=lambda d: d.title or "")
    assert len(drafts) == 2
    one, two = drafts
    assert one.title == "orphan-one.jpg"
    assert one.ts == datetime(2024, 3, 5, 1, 0, tzinfo=UTC)  # EXIF +05:30 applied
    assert one.lat == pytest.approx(40.2)
    assert one.camera_model == "SC-9"
    assert two.ts is None
    assert len(_potluck_warnings(caplog)) == 1  # latched per directory


def test_shared_base_sidecar_is_not_assumed_for_edited_variants(tmp_path: Path) -> None:
    """``X-edited.jpg`` never prefix-matches ``X.jpg``'s sidecar — it imports
    sidecar-less (the real export contains zero -edited files; without real
    evidence the parser must not guess at sharing)."""
    members = {
        f"{_ALBUM}/sunset.jpg": tiny_image(color=(1, 2, 3)),
        f"{_ALBUM}/sunset-edited.jpg": tiny_image(color=(4, 5, 6)),
        f"{_ALBUM}/sunset.jpg.supplemental-metadata.json": sidecar_json(
            "sunset.jpg", taken_epoch=_EPOCH
        ),
    }
    drafts = {d.title: d for d in _drafts(tmp_path, members)}
    assert set(drafts) == {"sunset.jpg", "sunset-edited.jpg"}
    assert drafts["sunset-edited.jpg"].ts is None  # no sidecar, no EXIF


# ---------------------------------------------------------------------------
# Cross-album dedup
# ---------------------------------------------------------------------------


def test_cross_album_duplicate_yields_byte_equal_drafts(tmp_path: Path) -> None:
    """Acceptance #3: identity is the byte hash, so the second album's copy
    re-yields the FIRST occurrence's draft verbatim — the engine then counts
    it as a duplicate, and the first album wins meta."""
    media = tiny_image(color=(120, 10, 200))
    sidecar = sidecar_json("dup.jpg", taken_epoch=_EPOCH, geo=(40.3, -75.3, 5.0))
    members = {
        f"{_ALBUM}/dup.jpg": media,
        f"{_ALBUM}/dup.jpg.supplemental-metadata.json": sidecar,
        f"{_NAMED}/dup.jpg": media,
        f"{_NAMED}/dup.jpg.supplemental-metadata.json": sidecar,
    }
    drafts = _drafts(tmp_path, members)
    assert len(drafts) == 2
    first, second = drafts
    assert first == second  # byte-equal → engine hash-duplicate
    assert first.external_id == f"photos:{hashlib.sha256(media).hexdigest()[:16]}"
    assert first.meta["album"] == "Photos from 2024"  # first occurrence wins


def test_cross_album_duplicate_survives_sidecar_drift(tmp_path: Path) -> None:
    """Even when the two albums' sidecars DIFFER (never observed in the real
    export, but nothing guarantees it), the copies must still collapse to one
    engine duplicate — the first draft is re-yielded, not rebuilt."""
    media = tiny_image(color=(7, 77, 177))
    members = {
        f"{_ALBUM}/drift.jpg": media,
        f"{_ALBUM}/drift.jpg.supplemental-metadata.json": sidecar_json(
            "drift.jpg", description="first", taken_epoch=_EPOCH
        ),
        f"{_NAMED}/drift.jpg": media,
        f"{_NAMED}/drift.jpg.supplemental-metadata.json": sidecar_json(
            "drift.jpg", description="second", taken_epoch=_EPOCH
        ),
    }
    drafts = _drafts(tmp_path, members)
    assert len(drafts) == 2
    assert drafts[0] == drafts[1]
    assert drafts[0].text == "first"


# ---------------------------------------------------------------------------
# Album metadata
# ---------------------------------------------------------------------------


def test_album_metadata_feeds_album_and_never_imports(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import json

    metadata = json.dumps(
        {
            "title": "Synthetic Fixture Album",
            "sharedAlbumComments": [
                {
                    "text": "a comment that must never become an item",
                    "creationTime": {"timestamp": str(_EPOCH), "formatted": "x"},
                    "contentOwnerName": "Bo Sample",
                }
            ],
        }
    ).encode()
    members = {
        f"{_NAMED}/metadata.json": metadata,
        f"{_NAMED}/album-photo.jpg": tiny_image(),
        f"{_NAMED}/album-photo.jpg.supplemental-metadata.json": sidecar_json(
            "album-photo.jpg", taken_epoch=_EPOCH
        ),
    }
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(tmp_path, members)
    [draft] = drafts
    assert draft.meta["album"] == "Synthetic Fixture Album"
    assert not _potluck_warnings(caplog)  # metadata.json is NOT an unpaired sidecar


def test_unreadable_album_metadata_warns_and_falls_back_to_dir_name(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    members = {
        f"{_NAMED}/metadata.json": b"{not json",
        f"{_NAMED}/album-photo.jpg": tiny_image(),
        f"{_NAMED}/album-photo.jpg.supplemental-metadata.json": sidecar_json(
            "album-photo.jpg", taken_epoch=_EPOCH
        ),
    }
    with caplog.at_level(logging.WARNING):
        [draft] = _drafts(tmp_path, members)
    assert draft.meta["album"] == "Synth Album"
    assert len(_potluck_warnings(caplog)) == 1


def test_product_root_json_skipped_silently(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A json directly under Google Photos/ (print-subscriptions style) is
    product metadata, not a sidecar — skipped without noise."""
    members = {
        "Takeout/Google Photos/print-subscriptions.json": b'{"printSubscriptions": []}',
        f"{_ALBUM}/pic.jpg": tiny_image(),
        f"{_ALBUM}/pic.jpg.supplemental-metadata.json": sidecar_json("pic.jpg", taken_epoch=_EPOCH),
    }
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(tmp_path, members)
    assert len(drafts) == 1
    assert not _potluck_warnings(caplog)


# ---------------------------------------------------------------------------
# Coordinate + timestamp precedence
# ---------------------------------------------------------------------------


def test_zero_geo_sentinel_never_emits_coordinates(tmp_path: Path) -> None:
    """geoData 0.0/0.0 means "no GPS" — items must NEVER carry lat/lon 0,0."""
    members = {
        f"{_ALBUM}/nowhere.jpg": tiny_image(),
        f"{_ALBUM}/nowhere.jpg.supplemental-metadata.json": sidecar_json(
            "nowhere.jpg",
            taken_epoch=_EPOCH,  # geo defaults to the 0.0 sentinel
        ),
    }
    [draft] = _drafts(tmp_path, members)
    assert draft.lat is None
    assert draft.lon is None
    assert draft.gps_alt is None


def test_geo_precedence_sidecar_beats_exif(tmp_path: Path) -> None:
    members = {
        f"{_ALBUM}/where.jpg": tiny_image(gps=(41.0, -76.0, 9.0)),
        f"{_ALBUM}/where.jpg.supplemental-metadata.json": sidecar_json(
            "where.jpg",
            taken_epoch=_EPOCH,
            geo=(40.1, -75.1, 30.0),
            geo_exif=(40.2, -75.2, 20.0),
        ),
    }
    [draft] = _drafts(tmp_path, members)
    assert (draft.lat, draft.lon, draft.gps_alt) == (40.1, -75.1, 30.0)


def test_geo_precedence_geodataexif_second(tmp_path: Path) -> None:
    members = {
        f"{_ALBUM}/where.jpg": tiny_image(gps=(41.0, -76.0, 9.0)),
        f"{_ALBUM}/where.jpg.supplemental-metadata.json": sidecar_json(
            "where.jpg", taken_epoch=_EPOCH, geo_exif=(40.2, -75.2, 20.0)
        ),
    }
    [draft] = _drafts(tmp_path, members)
    assert (draft.lat, draft.lon, draft.gps_alt) == (40.2, -75.2, 20.0)


def test_geo_precedence_exif_gps_last(tmp_path: Path) -> None:
    members = {
        f"{_ALBUM}/where.jpg": tiny_image(gps=(41.0, -76.0, 9.0)),
        f"{_ALBUM}/where.jpg.supplemental-metadata.json": sidecar_json(
            "where.jpg", taken_epoch=_EPOCH
        ),
    }
    [draft] = _drafts(tmp_path, members)
    assert draft.lat == pytest.approx(41.0)
    assert draft.lon == pytest.approx(-76.0)
    assert draft.gps_alt == pytest.approx(9.0)


def test_exif_null_island_rejected(tmp_path: Path) -> None:
    members = {f"{_ALBUM}/null-island.jpg": tiny_image(gps=(0.0, 0.0, 0.0))}
    [draft] = _drafts(tmp_path, members)
    assert draft.lat is None and draft.lon is None


def test_ts_precedence_taken_then_exif_then_creation(tmp_path: Path) -> None:
    exif_taken, exif_offset = "2024:03:05 06:30:00", "+05:30"
    members = {
        f"{_ALBUM}/a-taken.jpg": tiny_image(taken=exif_taken, offset=exif_offset),
        f"{_ALBUM}/a-taken.jpg.supplemental-metadata.json": sidecar_json(
            "a-taken.jpg", taken_epoch=_EPOCH, creation_epoch=_EPOCH + 999
        ),
        f"{_ALBUM}/b-exif.jpg": tiny_image(color=(2, 2, 2), taken=exif_taken, offset=exif_offset),
        f"{_ALBUM}/b-exif.jpg.supplemental-metadata.json": sidecar_json(
            "b-exif.jpg", creation_epoch=_EPOCH + 999
        ),
        f"{_ALBUM}/c-creation.jpg": tiny_image(color=(3, 3, 3)),
        f"{_ALBUM}/c-creation.jpg.supplemental-metadata.json": sidecar_json(
            "c-creation.jpg", creation_epoch=_EPOCH + 999
        ),
    }
    by_title = {d.title: d for d in _drafts(tmp_path, members)}
    assert by_title["a-taken.jpg"].ts == _TAKEN
    assert by_title["b-exif.jpg"].ts == datetime(2024, 3, 5, 1, 0, tzinfo=UTC)  # +05:30 applied
    assert by_title["c-creation.jpg"].ts == datetime.fromtimestamp(_EPOCH + 999, tz=UTC)


def test_exif_datetime_without_offset_reads_as_utc(tmp_path: Path) -> None:
    """The whatsapp/gmail unknown-zone policy: a naive EXIF DateTimeOriginal
    is taken as UTC."""
    members = {f"{_ALBUM}/naive.jpg": tiny_image(taken="2024:03:05 06:30:00")}
    [draft] = _drafts(tmp_path, members)
    assert draft.ts == datetime(2024, 3, 5, 6, 30, tzinfo=UTC)


def test_malformed_exif_datetime_falls_through(tmp_path: Path) -> None:
    members = {
        f"{_ALBUM}/badclock.jpg": tiny_image(taken="0000:00:00 00:00:00"),
        f"{_ALBUM}/badclock.jpg.supplemental-metadata.json": sidecar_json(
            "badclock.jpg", creation_epoch=_EPOCH
        ),
    }
    [draft] = _drafts(tmp_path, members)
    assert draft.ts == _TAKEN  # fell through to creationTime


# ---------------------------------------------------------------------------
# Videos and non-probed formats
# ---------------------------------------------------------------------------


def test_video_imports_sidecar_only(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    from potluck.testing.photos import stub_mp4

    video = stub_mp4()
    members = {
        f"{_ALBUM}/clip.mp4": video,
        f"{_ALBUM}/clip.mp4.supplemental-metadata.json": sidecar_json(
            "clip.mp4", taken_epoch=_EPOCH, geo=(40.4, -75.4, 12.0)
        ),
    }
    with caplog.at_level(logging.WARNING):
        [draft] = _drafts(tmp_path, members)
    assert draft.meta["type"] == "video"
    assert draft.mime == "video/mp4"
    assert draft.width is None and draft.height is None
    assert draft.lat == 40.4
    assert draft.sha256 == hashlib.sha256(video).hexdigest()
    assert not _potluck_warnings(caplog)  # mp4 is never probed, so never warns


def test_unsupported_image_extension_skips_probe_silently(tmp_path: Path) -> None:
    members = {
        f"{_ALBUM}/apple-shot.heic": b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32,
        f"{_ALBUM}/apple-shot.heic.supplemental-metadata.json": sidecar_json(
            "apple-shot.heic", taken_epoch=_EPOCH
        ),
    }
    [draft] = _drafts(tmp_path, members)
    assert draft.meta["type"] == "photo"
    assert draft.width is None  # Pillow cannot decode HEIC without a plugin — not probed


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------


def test_malformed_image_warns_and_imports_facts_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    junk = b"this is not a jpeg at all, but it has jpg extension" * 4
    members = {
        f"{_ALBUM}/broken.jpg": junk,
        f"{_ALBUM}/broken.jpg.supplemental-metadata.json": sidecar_json(
            "broken.jpg", taken_epoch=_EPOCH
        ),
    }
    with caplog.at_level(logging.WARNING):
        [draft] = _drafts(tmp_path, members)
    assert draft.sha256 == hashlib.sha256(junk).hexdigest()
    assert draft.size_bytes == len(junk)
    assert draft.width is None
    assert draft.mime == "image/jpeg"  # extension fallback
    assert draft.ts == _TAKEN
    warnings = _potluck_warnings(caplog)
    assert len(warnings) == 1
    assert "broken.jpg" in warnings[0]


def test_malformed_sidecar_warns_media_still_imports(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    members = {
        f"{_ALBUM}/pic.jpg": tiny_image(),
        f"{_ALBUM}/pic.jpg.supplemental-metadata.json": b"{broken",
    }
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(tmp_path, members)
    assert len(drafts) == 1
    assert drafts[0].title == "pic.jpg"
    assert any("pic.jpg" in w for w in _potluck_warnings(caplog))


# ---------------------------------------------------------------------------
# Meta and text composition
# ---------------------------------------------------------------------------


def test_meta_and_text_composition(tmp_path: Path) -> None:
    members = {
        f"{_ALBUM}/social.jpg": tiny_image(),
        f"{_ALBUM}/social.jpg.supplemental-metadata.json": sidecar_json(
            "social.jpg",
            taken_epoch=_EPOCH,
            description="synthetic caption",
            people=("Ada Example", "Bo Sample"),
            favorited=True,
            url="https://photos.google.com/photo/synthetic-1",
            device_folder="Camera",
            app_source="test.synthetic.app",
        ),
        f"{_ALBUM}/plain.jpg": tiny_image(color=(5, 5, 5)),
        f"{_ALBUM}/plain.jpg.supplemental-metadata.json": sidecar_json(
            "plain.jpg", taken_epoch=_EPOCH
        ),
    }
    by_title = {d.title: d for d in _drafts(tmp_path, members)}
    social = by_title["social.jpg"]
    assert social.text == "synthetic caption\nWith Ada Example, Bo Sample"
    assert social.meta["favorited"] is True
    assert social.meta["url"] == "https://photos.google.com/photo/synthetic-1"
    assert social.meta["device_folder"] == "Camera"
    assert social.meta["app_source"] == "test.synthetic.app"
    plain = by_title["plain.jpg"]
    assert plain.text is None
    assert "favorited" not in plain.meta  # only stored when true


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "matches"),
    [
        ("Takeout/Google Photos/Photos from 2024/x.jpg", True),
        ("Google Photos/album/x.jpg", True),
        ("deep/nesting/Takeout/Google Photos/a/x.json", True),
        ("Takeout/Google Play Store/Library.json", False),
        ("Takeout/Drive/My Photos/vacation-snap.jpg", False),
        ("My Google Photos/album/x.jpg", False),
        ("Takeout/Google Play Books/photo.jpg", False),
    ],
)
def test_detection_glob_precision(name: str, matches: bool) -> None:
    plugin = discover()["photos"]
    assert plugin.detect.matches(name) is matches


# ---------------------------------------------------------------------------
# Generator corpus end-to-end (parse level)
# ---------------------------------------------------------------------------


def test_generated_corpus_matches_closed_forms(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    archive_path = write_photos_takeout(tmp_path, 30, seed=7, fmt="zip")
    with caplog.at_level(logging.WARNING):
        drafts = list(parse(open_archive(archive_path), ParseContext()))

    unique = {d.external_id: d for d in drafts}
    assert len(unique) == expected_item_count(30)
    assert len(drafts) == expected_item_count(30) + 1  # + the cross-album re-yield
    videos = [d for d in unique.values() if d.meta["type"] == "video"]
    assert len(videos) == expected_video_count(30)
    with_coords = [d for d in unique.values() if d.lat is not None]
    assert len(with_coords) == expected_coordinate_count(30)
    assert all(d.lat != 0.0 or d.lon != 0.0 for d in with_coords)
    assert all(d.title for d in unique.values())

    # Exactly two expected warnings: the sidecar-less orphan (dir latch) and
    # the media-less ghost sidecar.
    warnings = _potluck_warnings(caplog)
    assert len(warnings) == 2
    assert any("orphan" in w for w in warnings)
    assert any("ghost" in w for w in warnings)


def test_multi_part_set_pairs_across_parts(tmp_path: Path) -> None:
    """Sidecar and media may land in DIFFERENT parts of a multi-part set —
    the two passes chain all parts, so pairing survives the split."""
    part_paths = write_photos_takeout_parts(tmp_path, 6, seed=7, parts=2)
    drafts = list(parse(open_archive(part_paths[0]), ParseContext()))
    unique = {d.external_id: d for d in drafts}
    assert len(unique) == expected_item_count(6)
    # Every item is dated: paired ones by their sidecar (photoTakenTime or
    # creationTime), the sidecar-less orphan by its EXIF DateTimeOriginal —
    # so an unpaired sidecar anywhere would surface as an undated item here.
    assert all(d.ts is not None for d in unique.values())


# ---------------------------------------------------------------------------
# Dependency floor
# ---------------------------------------------------------------------------


def test_pillow_floor_matches_pyproject() -> None:
    """sources/photos.py imports PIL at top of file; plugin discovery skips
    modules that fail to import, so a resolution below the proven floor
    (10.4.0 — first cp313 release, every used API verified against it via
    ``uv run --with pillow==10.4.0``) would silently drop the whole photos
    source. The declared floor must stay the proven one."""
    pyproject = Path(__file__).resolve().parents[4] / "pyproject.toml"
    dependencies = tomllib.loads(pyproject.read_text())["project"]["dependencies"]
    [spec] = [dep for dep in dependencies if dep.startswith("pillow")]
    assert spec == "pillow>=10.4"
