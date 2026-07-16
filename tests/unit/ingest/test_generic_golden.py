"""Golden test (#150): the committed generic-folder fixture yields exact
results across all three generic sources, and tier fallback holds end to end.

The fixture is generator output only (see tests/fixtures/README.md); the
regeneration one-liner lives in potluck/testing/generic.py. Committed as a
DIRECTORY like every other fixture, so member mtimes are checkout-time —
mtime-derived timestamps are pinned as PRESENT here and pinned exactly in
the source unit tests over generated zip/tgz archives (whose mtimes are
deterministic).
"""

import logging
from pathlib import Path

import pytest

from potluck.services.context import AppContext
from potluck.services.imports import import_path
from potluck.services.search import search
from potluck.testing.archives import write_archive
from potluck.testing.generic import generic_members
from potluck.testing.mbox import synthetic_mbox_messages
from potluck.testing.whatsapp import synthetic_chat_lines

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "generic" / "generic-synth-001"

GOLDEN_BULK = 8  # fixture regeneration arguments, pinned for the record
GOLDEN_SEED = 7

GOLDEN_NOTES = 15  # 8 bulk + 7 fixed specials
GOLDEN_IMAGES = 4
GOLDEN_IMAGE_DUPLICATES = 1  # Pictures/copy/exif-gps.jpg
GOLDEN_EMAILS = 6
GOLDEN_NOID_EMAILS = 2  # generator entries without a Message-ID at seed 7

# Identity stability anchors. The image eid pins the committed fixture's
# exact bytes (images:<sha256[:16]>) — it can only change if the fixture is
# regenerated under a different Pillow encoder or the identity policy moves;
# bump parser_version and say so in the commit if the policy moves. The note
# and email eids pin the path/Message-ID recipes.
GOLDEN_EXIF_EID = "images:bec5bd38506cd602"
GOLDEN_PLAN_EID = "notes:Notes/journal/synth-plan.md"
GOLDEN_MAIL_EID = "mbox:mid:synth-7-000000@potluck.test"


def test_fixture_exists() -> None:
    for member in (
        "Notes/journal/synth-plan.md",
        "Notes/WhatsApp Chat ideas.md",
        "Pictures/2024/exif-gps.jpg",
        "Pictures/copy/exif-gps.jpg",
        "Pictures/corrupt.jpg",
        "mail/archive.mbox",
        "misc/data.csv",
    ):
        assert (FIXTURE / member).is_file(), member


def test_golden_import_counts(ctx: AppContext, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        runs = import_path(ctx, FIXTURE)
    assert [run.source for run in runs] == ["images", "mbox", "notes"]
    assert all(run.status == "completed" for run in runs)
    by_source = {run.source: run for run in runs}
    assert by_source["images"].items_new == GOLDEN_IMAGES
    assert by_source["images"].items_duplicate == GOLDEN_IMAGE_DUPLICATES
    assert by_source["mbox"].items_new == GOLDEN_EMAILS
    assert by_source["notes"].items_new == GOLDEN_NOTES

    # Exactly one warning across all three runs: the corrupt-image skip.
    warnings = [r.message for r in caplog.records if r.name.startswith("potluck")]
    assert len(warnings) == 1, warnings
    assert "corrupt.jpg" in warnings[0] and "skipped" in warnings[0]

    with ctx.db.read() as conn:
        kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM items GROUP BY kind").fetchall())
        media_rows = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        email_rows = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    assert kinds == {"email": GOLDEN_EMAILS, "note": GOLDEN_NOTES, "photo": GOLDEN_IMAGES}
    assert media_rows == GOLDEN_IMAGES
    assert email_rows == GOLDEN_EMAILS


def test_golden_image_identity_and_satellite(ctx: AppContext) -> None:
    """The dedup pair lands as ONE item (first path wins the title), with
    EXIF facts on the item and byte facts in the media satellite."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        row = conn.execute(
            """SELECT i.title, i.ts, i.lat, i.lon, m.width, m.height, m.camera_make,
                      m.mime, m.sha256
               FROM items i JOIN media m ON m.item_id = i.id
               WHERE i.external_id = ?""",
            (GOLDEN_EXIF_EID,),
        ).fetchone()
        copies = conn.execute(
            "SELECT COUNT(*) FROM items WHERE title LIKE '%exif-gps%'"
        ).fetchone()[0]
    assert row is not None
    assert copies == 1
    assert row["title"] == "exif-gps.jpg"
    assert row["ts"].startswith("2024-03-01T08:00:00")
    assert (row["lat"], row["lon"]) == (40.71, -74.29)
    assert row["width"] == 32 and row["height"] == 24
    assert row["camera_make"] == "SynthCam"
    assert row["mime"] == "image/jpeg"
    assert f"images:{row['sha256'][:16]}" == GOLDEN_EXIF_EID


def test_golden_note_titles_and_identity(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        titles = dict(
            conn.execute("SELECT external_id, title FROM items WHERE kind = 'note'").fetchall()
        )
        undated = conn.execute("SELECT COUNT(*) FROM items WHERE ts IS NULL").fetchone()[0]
    assert titles[GOLDEN_PLAN_EID] == "Synthetic Plan"  # H1 wins
    assert titles["notes:Notes/no-heading.md"] == "no-heading"  # stem fallback
    assert titles["notes:Notes/todo-list.txt"] == "todo-list"
    assert titles["notes:Notes/guide.markdown"] == "Field Guide"
    assert titles["notes:Notes/empty.txt"] == "empty"  # 0-byte file, title-only
    # The WhatsApp-named .md is a NOTE — its name baits the whatsapp glob but
    # the extension misses it (detection precision, not tier fallback).
    assert titles["notes:Notes/WhatsApp Chat ideas.md"] == "WhatsApp Chat ideas"
    # Directory fixture: every member carries a checkout mtime, emails carry
    # header dates, EXIF covers the jpg — nothing is undated.
    assert undated == 0


def test_golden_email_identities(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        mids = conn.execute(
            "SELECT COUNT(*) FROM items WHERE external_id LIKE 'mbox:mid:%'"
        ).fetchone()[0]
        noids = conn.execute(
            "SELECT COUNT(*) FROM items WHERE external_id LIKE 'mbox:noid:%'"
        ).fetchone()[0]
        first = conn.execute(
            "SELECT COUNT(*) FROM items WHERE external_id = ?", (GOLDEN_MAIL_EID,)
        ).fetchone()[0]
    assert mids == GOLDEN_EMAILS - GOLDEN_NOID_EMAILS
    assert noids == GOLDEN_NOID_EMAILS
    assert first == 1


def test_golden_no_null_island_single_coordinate(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        zeros = conn.execute("SELECT COUNT(*) FROM items WHERE lat = 0.0 AND lon = 0.0").fetchone()
        coords = conn.execute(
            "SELECT COUNT(*) FROM items WHERE lat IS NOT NULL AND lon IS NOT NULL"
        ).fetchone()
    assert zeros[0] == 0
    assert coords[0] == 1  # only the EXIF-GPS jpg


def test_golden_search_finds_notes(ctx: AppContext) -> None:
    from potluck.models.items import ItemKind
    from potluck.models.search import SearchRequest

    import_path(ctx, FIXTURE)
    response = search(ctx, SearchRequest(query='kind:note "Synthetic Plan"'))
    assert len(response.hits) >= 1
    assert all(hit.kind is ItemKind.NOTE for hit in response.hits)
    assert not response.warnings


def test_golden_reimport_is_noop(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    runs = import_path(ctx, FIXTURE)
    by_source = {run.source: run for run in runs}
    assert all(run.items_new == 0 and run.items_updated == 0 for run in runs)
    assert by_source["images"].items_duplicate == GOLDEN_IMAGES + GOLDEN_IMAGE_DUPLICATES
    assert by_source["mbox"].items_duplicate == GOLDEN_EMAILS
    assert by_source["notes"].items_duplicate == GOLDEN_NOTES


# ---------------------------------------------------------------------------
# Single-file imports (#150 acceptance: bare files work end to end)
# ---------------------------------------------------------------------------


def test_bare_markdown_file_imports(ctx: AppContext, tmp_path: Path) -> None:
    note = tmp_path / "plan.md"
    note.write_bytes(b"# Bare Plan\nbody\n")
    [run] = import_path(ctx, note)
    assert run.source == "notes"
    assert run.items_new == 1
    with ctx.db.read() as conn:
        row = conn.execute("SELECT external_id, title FROM items").fetchone()
    assert row["external_id"] == "notes:plan.md"
    assert row["title"] == "Bare Plan"


def test_bare_mbox_file_imports(ctx: AppContext, tmp_path: Path) -> None:
    mbox = tmp_path / "archive-2024.mbox"
    mbox.write_bytes(b"".join(synthetic_mbox_messages(2, seed=GOLDEN_SEED)))
    [run] = import_path(ctx, mbox)
    assert run.source == "mbox"
    assert run.items_new == 2


# ---------------------------------------------------------------------------
# Tier fallback end to end
# ---------------------------------------------------------------------------


def test_mixed_archive_imports_only_the_specific_source(ctx: AppContext, tmp_path: Path) -> None:
    """THE degradation the design accepts (#150): a folder holding a
    recognized WhatsApp export plus loose notes/images/mail imports only the
    export; the loose files need a direct import of their subfolder."""
    members = generic_members(2, seed=GOLDEN_SEED)
    chat = "\n".join(synthetic_chat_lines(5, seed=GOLDEN_SEED)) + "\n"
    members["WhatsApp Chat with Ada Example.txt"] = chat.encode()
    mixed = write_archive(tmp_path / "mixed.zip", members, "zip")

    runs = import_path(ctx, mixed)
    assert [run.source for run in runs] == ["whatsapp"]
    with ctx.db.read() as conn:
        kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM items GROUP BY kind").fetchall())
    assert set(kinds) == {"message"}  # no note/photo/email leaked past the tier
