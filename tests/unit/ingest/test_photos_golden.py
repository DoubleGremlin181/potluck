"""Golden test (#149): the committed Google Photos fixture yields exact results.

The fixture is generator output only (see tests/fixtures/README.md); the
regeneration one-liner lives in potluck/testing/photos.py. 12 bulk media in
the auto album (jpg/png/webp/mp4 by modular rules, sidecar geo and EXIF-GPS
variants, truncated sidecar names) plus the fixed specials: the cross-album
byte-duplicate pair, the (N) pathology pair, the sidecar-less orphan, the
media-less ghost sidecar, the truncation showcases, the ts-precedence pair,
a GPS video, the named album with metadata.json, and every detection decoy.
"""

import logging
from pathlib import Path

import pytest

from potluck.models.items import ItemKind
from potluck.models.search import SearchRequest
from potluck.services.context import AppContext
from potluck.services.imports import import_path
from potluck.services.search import search
from potluck.testing.photos import (
    expected_coordinate_count,
    expected_item_count,
    expected_video_count,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "photos" / "photos-synth-001"

GOLDEN_BULK = 12  # fixture regeneration arguments, pinned for the record
GOLDEN_SEED = 7

# Closed-form results for count=12, pinned as literals so BOTH generator
# drift and parser drift are caught.
GOLDEN_COUNT = 24
GOLDEN_VIDEOS = 3
GOLDEN_PHOTOS = GOLDEN_COUNT - GOLDEN_VIDEOS
GOLDEN_COORDS = 11
GOLDEN_IN_RUN_DUPLICATES = 1  # the cross-album byte-duplicate pair
GOLDEN_FAVORITED = 2
GOLDEN_ADA_HITS = 2  # items whose text names "Ada Example"

# Identity stability anchors: photos:<sha256(media bytes)[:16]>. These pin the
# committed fixture's exact bytes — they can only change if the fixture is
# regenerated (a different Pillow encoder) or the identity policy changes;
# bump parser_version and say so in the commit if the policy moves.
GOLDEN_DUP_EID = "photos:00413b139abdebc6"
GOLDEN_VIDEO_EID = "photos:f3b3bd2ddfcb9548"


def test_fixture_exists() -> None:
    for member in (
        "Takeout/Google Photos/Photos from 2024/dup-across-albums.jpg",
        "Takeout/Google Photos/Synth Album/dup-across-albums.jpg",
        "Takeout/Google Photos/Synth Album/metadata.json",
        "Takeout/Google Photos/print-subscriptions.json",
        "Takeout/Google Play Store/Library.json",
        "Takeout/Drive/My Photos/vacation-snap.jpg",
    ):
        assert (FIXTURE / member).is_file(), member


def test_closed_forms_match_pinned_literals() -> None:
    assert expected_item_count(GOLDEN_BULK) == GOLDEN_COUNT
    assert expected_video_count(GOLDEN_BULK) == GOLDEN_VIDEOS
    assert expected_coordinate_count(GOLDEN_BULK) == GOLDEN_COORDS


def test_golden_import_counts(ctx: AppContext, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        [run] = import_path(ctx, FIXTURE)
    assert run.source == "photos"
    assert run.status == "completed"
    assert run.items_new == GOLDEN_COUNT
    assert run.items_duplicate == GOLDEN_IN_RUN_DUPLICATES

    # Exactly the two expected warnings: the sidecar-less orphan and the
    # media-less ghost sidecar — everything else pairs and parses silently.
    warnings = [r.message for r in caplog.records if r.name.startswith("potluck")]
    assert len(warnings) == 2, warnings
    assert any("orphan" in w for w in warnings)
    assert any("ghost" in w for w in warnings)

    with ctx.db.read() as conn:
        kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM items GROUP BY kind").fetchall())
        types = dict(
            conn.execute(
                "SELECT json_extract(meta, '$.type'), COUNT(*) FROM items GROUP BY 1"
            ).fetchall()
        )
        satellite_rows = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    assert kinds == {"photo": GOLDEN_COUNT}
    assert types == {"photo": GOLDEN_PHOTOS, "video": GOLDEN_VIDEOS}
    assert satellite_rows == GOLDEN_COUNT


def test_golden_cross_album_duplicate_ingested_once(ctx: AppContext) -> None:
    """THE acceptance criterion: the same bytes in two albums land as ONE
    item (engine duplicate), and the first album wins meta."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        rows = conn.execute(
            """SELECT external_id, json_extract(meta, '$.album') FROM items
               WHERE title = 'dup-across-albums.jpg'"""
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == GOLDEN_DUP_EID
    assert rows[0][1] == "Photos from 2024"


def test_golden_media_satellite_fidelity(ctx: AppContext) -> None:
    """#149's acceptance made executable: dimensions, camera, and byte facts
    land in the media satellite; EXIF/sidecar GPS lands on items.lat/lon."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        row = conn.execute(
            """SELECT m.width, m.height, m.camera_make, m.mime, m.size_bytes, m.sha256,
                      i.lat, i.lon
               FROM media m JOIN items i ON i.id = m.item_id
               WHERE i.external_id = ?""",
            (GOLDEN_DUP_EID,),
        ).fetchone()
    assert row is not None
    assert row["width"] == 32 and row["height"] == 24
    assert row["camera_make"] == "SynthCam"
    assert row["mime"] == "image/jpeg"
    assert row["size_bytes"] > 0
    assert f"photos:{row['sha256'][:16]}" == GOLDEN_DUP_EID
    assert (row["lat"], row["lon"]) == (40.2, -75.2)

    with ctx.db.read() as conn:
        video = conn.execute(
            """SELECT m.width, m.mime, json_extract(i.meta, '$.type') AS t
               FROM media m JOIN items i ON i.id = m.item_id
               WHERE i.external_id = ?""",
            (GOLDEN_VIDEO_EID,),
        ).fetchone()
    assert video is not None
    assert video["width"] is None
    assert video["mime"] == "video/mp4"
    assert video["t"] == "video"


def test_golden_no_null_island_and_all_titled(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        zeros = conn.execute("SELECT COUNT(*) FROM items WHERE lat = 0.0 AND lon = 0.0").fetchone()
        coords = conn.execute(
            "SELECT COUNT(*) FROM items WHERE lat IS NOT NULL AND lon IS NOT NULL"
        ).fetchone()
        untitled = conn.execute("SELECT COUNT(*) FROM items WHERE title IS NULL").fetchone()
        undated = conn.execute("SELECT COUNT(*) FROM items WHERE ts IS NULL").fetchone()
    assert zeros[0] == 0  # the 0.0/0.0 sentinel must never become coordinates
    assert coords[0] == GOLDEN_COORDS
    assert untitled[0] == 0
    assert undated[0] == 0


def test_golden_favorited_only_when_true(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        favorited = conn.execute(
            "SELECT COUNT(*) FROM items WHERE json_extract(meta, '$.favorited')"
        ).fetchone()[0]
        false_keys = conn.execute(
            "SELECT COUNT(*) FROM items WHERE json_extract(meta, '$.favorited') = false"
        ).fetchone()[0]
    assert favorited == GOLDEN_FAVORITED
    assert false_keys == 0


def test_golden_search_finds_photos(ctx: AppContext) -> None:
    """kind:photo + a people name returns ranked photo hits — the FTS surface
    the vocabulary resolution promises."""
    import_path(ctx, FIXTURE)
    response = search(ctx, SearchRequest(query='kind:photo "Ada Example"'))
    assert len(response.hits) == GOLDEN_ADA_HITS
    assert all(hit.kind is ItemKind.PHOTO for hit in response.hits)
    assert not response.warnings


def test_golden_reimport_is_noop(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    [run2] = import_path(ctx, FIXTURE)
    assert run2.items_new == 0
    assert run2.items_duplicate == GOLDEN_COUNT + GOLDEN_IN_RUN_DUPLICATES
    assert run2.items_updated == 0
