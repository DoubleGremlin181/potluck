"""Engine satellite dispatch for media (#149): media rows ride the same batch
transaction, and item detail hydrates them."""

from datetime import UTC, datetime, timedelta

import pytest

from potluck.models.drafts import PhotoDraft
from potluck.services.context import AppContext
from potluck.services.items import get_item
from tests.conftest import ingest_email_drafts

_SHA_A = "a1" * 32
_SHA_B = "b2" * 32


def _photo(n: int, *, camera_model: str = "SC-1000", sha256: str = _SHA_A) -> PhotoDraft:
    return PhotoDraft(
        external_id=f"photos:{sha256[:16]}",
        ts=datetime(2024, 3, 1, 8, tzinfo=UTC) + timedelta(hours=n),
        title=f"synth-{n:04d}.jpg",
        lat=40.0512345,
        lon=-75.1598765,
        width=4032,
        height=3024,
        camera_make="SynthCam",
        camera_model=camera_model,
        gps_alt=12.5,
        mime="image/jpeg",
        size_bytes=123456,
        sha256=sha256,
        meta={"type": "photo", "album": "Photos from 2024"},
    )


def _video(n: int) -> PhotoDraft:
    return PhotoDraft(
        external_id=f"photos:{_SHA_B[:16]}",
        ts=datetime(2024, 3, 2, 9, tzinfo=UTC) + timedelta(hours=n),
        title=f"synth-vid-{n:04d}.mp4",
        mime="video/mp4",
        size_bytes=987654,
        sha256=_SHA_B,
        meta={"type": "video"},
    )


def _run(ctx: AppContext, *drafts: PhotoDraft) -> int:
    return ingest_email_drafts(ctx, *drafts, source_name="photos", path="/tmp/takeout.tgz")


def test_import_writes_media_satellite(ctx: AppContext) -> None:
    _run(ctx, _photo(1), _video(2))
    with ctx.db.read() as conn:
        rows = conn.execute(
            """SELECT m.width, m.height, m.camera_make, m.camera_model, m.gps_alt,
                      m.mime, m.size_bytes, m.sha256
               FROM media m JOIN items i ON i.id = m.item_id
               ORDER BY i.ts"""
        ).fetchall()
    assert len(rows) == 2
    photo, video = rows
    assert photo["width"] == 4032
    assert photo["height"] == 3024
    assert photo["camera_make"] == "SynthCam"
    assert photo["camera_model"] == "SC-1000"
    assert photo["gps_alt"] == 12.5
    assert photo["mime"] == "image/jpeg"
    assert photo["size_bytes"] == 123456
    assert photo["sha256"] == _SHA_A
    assert video["width"] is None
    assert video["camera_make"] is None
    assert video["mime"] == "video/mp4"
    assert video["sha256"] == _SHA_B


def test_items_row_carries_coordinates(ctx: AppContext) -> None:
    """#149 acceptance: EXIF/sidecar GPS lands on items.lat/lon (the base
    columns) for future spatial linking."""
    _run(ctx, _photo(1))
    with ctx.db.read() as conn:
        row = conn.execute("SELECT lat, lon FROM items").fetchone()
    assert row["lat"] == 40.0512345
    assert row["lon"] == -75.1598765


def test_exact_reimport_is_duplicate(ctx: AppContext) -> None:
    _run(ctx, _photo(1))
    import_id2 = _run(ctx, _photo(1))
    with ctx.db.read() as conn:
        imp = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id2,)).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    assert int(imp["items_duplicate"]) == 1
    assert count == 1


def test_satellite_field_change_reingests_as_update(ctx: AppContext) -> None:
    """Satellite fields live inside the content hash (extra_hash_parts), so a
    changed camera model (e.g. an EXIF fix in a later export of the same
    bytes) must be an UPDATE that rewrites the satellite row — never a
    duplicate that silently keeps the stale value."""
    _run(ctx, _photo(1, camera_model="SC-1000"))
    import_id2 = _run(ctx, _photo(1, camera_model="SC-2000"))

    with ctx.db.read() as conn:
        imp = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id2,)).fetchone()
        camera_model = conn.execute("SELECT camera_model FROM media").fetchone()[0]
        item_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert item_count == 1
    assert int(imp["items_updated"]) == 1
    assert camera_model == "SC-2000"


def test_get_item_hydrates_media_detail(ctx: AppContext) -> None:
    _run(ctx, _photo(1))
    with ctx.db.read() as conn:
        item_id = int(conn.execute("SELECT id FROM items").fetchone()[0])

    item = get_item(ctx, item_id)
    assert item.email is None
    assert item.message is None
    assert item.transaction is None
    assert item.location is None
    assert item.media is not None
    assert item.media.width == 4032
    assert item.media.height == 3024
    assert item.media.camera_make == "SynthCam"
    assert item.media.gps_alt == 12.5
    assert item.media.mime == "image/jpeg"
    assert item.media.size_bytes == 123456
    assert item.media.sha256 == _SHA_A


def test_photo_draft_rejects_malformed_sha256() -> None:
    """The sha256 is the identity anchor — anything but 64 lowercase hex is
    always a plugin bug and must die at the DTO boundary."""
    with pytest.raises(ValueError, match="sha256"):
        PhotoDraft(external_id="x", title="t", size_bytes=1, sha256="not-a-hash")
    with pytest.raises(ValueError, match="sha256"):
        PhotoDraft(external_id="x", title="t", size_bytes=1, sha256="AB" * 32)


def test_photo_draft_rejects_negative_size() -> None:
    with pytest.raises(ValueError, match="size_bytes"):
        PhotoDraft(external_id="x", title="t", size_bytes=-1, sha256=_SHA_A)


def test_photo_draft_requires_paired_dimensions() -> None:
    """width without height (or vice versa) is always a probing bug — an
    image has both dimensions or the probe failed entirely."""
    with pytest.raises(ValueError, match="width"):
        PhotoDraft(external_id="x", title="t", size_bytes=1, sha256=_SHA_A, width=100)


def test_photo_draft_rejects_out_of_range_coordinates() -> None:
    """Base lat/lon are narrowed to range-validated floats: a broken GPS
    parse must never enter storage as an impossible coordinate."""
    with pytest.raises(ValueError, match="less than or equal"):
        PhotoDraft(external_id="x", title="t", size_bytes=1, sha256=_SHA_A, lat=90.5, lon=0.0)
