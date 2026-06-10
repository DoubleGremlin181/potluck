"""Tests for the Google Keep source plugin and its _to_draft helper.

Testing private _to_draft is intentional: the skip policy is the public contract
of this module and must be covered at the unit level.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from potluck.ingest.plugins import detect_source, discover
from potluck.ingest.readers import open_archive
from potluck.ingest.sources.google_keep import _to_draft, parse
from potluck.services.context import AppContext
from potluck.services.imports import import_path
from potluck.testing.archives import write_archive
from potluck.testing.keep import synthetic_keep_notes, write_keep_takeout

REPO_ROOT = Path(__file__).resolve().parents[4]
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_note(**kwargs: Any) -> dict[str, Any]:
    """Minimal valid text note; keyword args override defaults."""
    base: dict[str, Any] = {
        "color": "DEFAULT",
        "isTrashed": False,
        "isPinned": False,
        "isArchived": False,
        "title": "Test Note",
        "textContent": "Hello world.",
        "userEditedTimestampUsec": 1_700_000_000_000_000,
        "createdTimestampUsec": 1_600_000_000_000_000,
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# _to_draft: text rendering
# ---------------------------------------------------------------------------


def test_text_note_textcontent_wins_over_listcontent() -> None:
    """textContent takes precedence over listContent when both are present."""
    data = _minimal_note(
        textContent="real text",
        listContent=[{"text": "list item", "textHtml": "<p>list item</p>", "isChecked": False}],
    )
    draft = _to_draft(data, "Takeout/Keep/note.json")
    assert draft is not None
    assert draft.text == "real text"


def test_checklist_rendering() -> None:
    """listContent renders as markdown checklist; empty-text items are skipped."""
    data = _minimal_note(
        textContent="",
        listContent=[
            {"text": "a", "textHtml": "<p>a</p>", "isChecked": True},
            {"text": "b", "textHtml": "<p>b</p>", "isChecked": False},
            {"text": "", "textHtml": "", "isChecked": False},  # empty → skipped
        ],
    )
    del data["textContent"]  # list note: no textContent key
    draft = _to_draft(data, "Takeout/Keep/list.json")
    assert draft is not None
    assert draft.text == "- [x] a\n- [ ] b"


def test_whitespace_only_list_item_skipped() -> None:
    """Whitespace-only list items are skipped after strip()."""
    data = _minimal_note(
        textContent="",
        listContent=[
            {"text": "real item", "textHtml": "<p>real item</p>", "isChecked": False},
            {"text": "   ", "textHtml": "", "isChecked": False},  # whitespace-only → skipped
            {"text": "\t\n", "textHtml": "", "isChecked": False},  # whitespace-only → skipped
        ],
    )
    del data["textContent"]  # list note
    draft = _to_draft(data, "Takeout/Keep/list.json")
    assert draft is not None
    assert draft.text == "- [ ] real item"


# ---------------------------------------------------------------------------
# _to_draft: skip policy
# ---------------------------------------------------------------------------


def test_trashed_note_skipped() -> None:
    """isTrashed=True → _to_draft returns None."""
    data = _minimal_note(isTrashed=True)
    assert _to_draft(data, "Takeout/Keep/trash.json") is None


def test_empty_note_skipped() -> None:
    """No text AND no title → _to_draft returns None."""
    data = _minimal_note(textContent="", title="")
    del data["textContent"]  # also test with key absent
    assert _to_draft(data, "Takeout/Keep/empty.json") is None


def test_note_with_only_title_not_skipped() -> None:
    """Title non-empty even with empty text → not skipped."""
    data = _minimal_note(textContent="", title="My Title")
    draft = _to_draft(data, "Takeout/Keep/title_only.json")
    assert draft is not None
    assert draft.title == "My Title"
    assert draft.text is None


# ---------------------------------------------------------------------------
# _to_draft: timestamps
# ---------------------------------------------------------------------------


def test_ts_prefers_user_edited_over_created() -> None:
    """userEditedTimestampUsec is used when truthy."""
    usec = 1_700_000_000_000_000
    data = _minimal_note(
        userEditedTimestampUsec=usec,
        createdTimestampUsec=1_600_000_000_000_000,
    )
    expected_ts = _EPOCH + timedelta(microseconds=usec)
    draft = _to_draft(data, "Takeout/Keep/ts.json")
    assert draft is not None
    assert draft.ts == expected_ts


def test_ts_falls_back_to_created_when_user_edited_zero() -> None:
    """userEditedTimestampUsec=0 → falls back to createdTimestampUsec."""
    created_usec = 1_600_000_000_000_000
    data = _minimal_note(userEditedTimestampUsec=0, createdTimestampUsec=created_usec)
    expected_ts = _EPOCH + timedelta(microseconds=created_usec)
    draft = _to_draft(data, "Takeout/Keep/ts.json")
    assert draft is not None
    assert draft.ts == expected_ts


def test_ts_none_when_both_zero() -> None:
    """Both timestamps 0 → ts=None (epoch-0 notes treated as missing timestamp)."""
    data = _minimal_note(userEditedTimestampUsec=0, createdTimestampUsec=0)
    draft = _to_draft(data, "Takeout/Keep/ts.json")
    assert draft is not None
    assert draft.ts is None


def test_ts_none_when_both_missing() -> None:
    """Both timestamp keys absent → ts=None."""
    data = _minimal_note()
    del data["userEditedTimestampUsec"]
    del data["createdTimestampUsec"]
    draft = _to_draft(data, "Takeout/Keep/ts.json")
    assert draft is not None
    assert draft.ts is None


def test_ts_microsecond_precision() -> None:
    """Conversion uses timedelta(microseconds=) — no float division that loses precision."""
    # A value that would lose precision under float division: 1_700_000_001_999_999
    usec = 1_700_000_001_999_999
    data = _minimal_note(userEditedTimestampUsec=usec, createdTimestampUsec=0)
    expected = _EPOCH + timedelta(microseconds=usec)
    draft = _to_draft(data, "Takeout/Keep/ts.json")
    assert draft is not None
    assert draft.ts == expected


# ---------------------------------------------------------------------------
# _to_draft: title
# ---------------------------------------------------------------------------


def test_title_none_when_empty_string() -> None:
    """Empty-string title → NoteDraft.title is None."""
    data = _minimal_note(title="")
    draft = _to_draft(data, "Takeout/Keep/note.json")
    assert draft is not None
    assert draft.title is None


def test_title_preserved_when_non_empty() -> None:
    """Non-empty title is stored as-is."""
    data = _minimal_note(title="My Important Note")
    draft = _to_draft(data, "Takeout/Keep/note.json")
    assert draft is not None
    assert draft.title == "My Important Note"


# ---------------------------------------------------------------------------
# _to_draft: external_id
# ---------------------------------------------------------------------------


def test_external_id_is_member_name() -> None:
    """external_id is set to the archive member name."""
    member_name = "Takeout/Keep/My Note.json"
    draft = _to_draft(_minimal_note(), member_name)
    assert draft is not None
    assert draft.external_id == member_name


# ---------------------------------------------------------------------------
# _to_draft: meta — present/meaningful fields
# ---------------------------------------------------------------------------


def test_labels_in_meta() -> None:
    """labels list → meta['labels'] contains name strings."""
    data = _minimal_note(labels=[{"name": "Work"}, {"name": "Personal"}])
    draft = _to_draft(data, "Takeout/Keep/note.json")
    assert draft is not None
    assert draft.meta["labels"] == ["Work", "Personal"]


def test_color_in_meta_only_when_not_default() -> None:
    """color='DEFAULT' excluded; any other value included."""
    # DEFAULT → not in meta
    draft_default = _to_draft(_minimal_note(color="DEFAULT"), "Takeout/Keep/note.json")
    assert draft_default is not None
    assert "color" not in draft_default.meta

    # GRAY → in meta
    draft_gray = _to_draft(_minimal_note(color="GRAY"), "Takeout/Keep/note.json")
    assert draft_gray is not None
    assert draft_gray.meta["color"] == "GRAY"


def test_is_pinned_in_meta_only_when_true() -> None:
    """isPinned=False excluded; isPinned=True included."""
    draft_false = _to_draft(_minimal_note(isPinned=False), "Takeout/Keep/note.json")
    assert draft_false is not None
    assert "isPinned" not in draft_false.meta

    draft_true = _to_draft(_minimal_note(isPinned=True), "Takeout/Keep/note.json")
    assert draft_true is not None
    assert draft_true.meta["isPinned"] is True


def test_is_archived_in_meta_only_when_true() -> None:
    """isArchived=False excluded; isArchived=True included."""
    draft_false = _to_draft(_minimal_note(isArchived=False), "Takeout/Keep/note.json")
    assert draft_false is not None
    assert "isArchived" not in draft_false.meta

    draft_true = _to_draft(_minimal_note(isArchived=True), "Takeout/Keep/note.json")
    assert draft_true is not None
    assert draft_true.meta["isArchived"] is True


def test_annotations_weblink_in_meta() -> None:
    """WEBLINK annotations included as {url,title,description}; non-WEBLINK excluded."""
    data = _minimal_note(
        annotations=[
            {
                "source": "WEBLINK",
                "url": "https://example.com/page",
                "title": "My Link",
                "description": "A description",
            },
            {
                "source": "OTHER_SOURCE",
                "url": "https://example.com/other",
                "title": "Other",
                "description": "Should be excluded",
            },
        ]
    )
    draft = _to_draft(data, "Takeout/Keep/note.json")
    assert draft is not None
    assert draft.meta["annotations"] == [
        {"url": "https://example.com/page", "title": "My Link", "description": "A description"}
    ]


def test_sharees_in_meta() -> None:
    """sharees copied verbatim into meta."""
    sharees: list[dict[str, Any]] = [{"email": "alice@potluck.test", "type": "collaborator"}]
    data = _minimal_note(sharees=sharees)
    draft = _to_draft(data, "Takeout/Keep/note.json")
    assert draft is not None
    assert draft.meta["sharees"] == sharees


def test_attachments_in_meta() -> None:
    """attachments copied verbatim into meta."""
    attachments: list[dict[str, Any]] = [
        {"filePath": "abc123.def456.jpg", "mimetype": "image/jpeg"}
    ]
    data = _minimal_note(attachments=attachments)
    draft = _to_draft(data, "Takeout/Keep/note.json")
    assert draft is not None
    assert draft.meta["attachments"] == attachments


# ---------------------------------------------------------------------------
# _to_draft: meta — exclusions
# ---------------------------------------------------------------------------


def test_text_content_html_excluded_from_meta() -> None:
    """textContentHtml is never stored in meta (bulky, derivable)."""
    data = _minimal_note(textContentHtml="<p>Hello world.</p>")
    draft = _to_draft(data, "Takeout/Keep/note.json")
    assert draft is not None
    assert "textContentHtml" not in draft.meta


def test_list_item_text_html_excluded_from_meta() -> None:
    """Per-item textHtml inside listContent is not propagated to top-level meta."""
    data = _minimal_note(
        textContent="",
        listContent=[{"text": "item", "textHtml": "<p>item</p>", "isChecked": False}],
        title="My List",
    )
    del data["textContent"]
    draft = _to_draft(data, "Takeout/Keep/list.json")
    assert draft is not None
    # textHtml is an item-level key, not a top-level key — must not end up in meta
    assert "textHtml" not in draft.meta
    # listContent itself is a known key and must not end up in meta
    assert "listContent" not in draft.meta


def test_unknown_top_level_key_copied_to_meta() -> None:
    """Keys not in the documented set are copied verbatim into meta."""
    data = _minimal_note(futureKey=42, anotherUnknown="hello")
    draft = _to_draft(data, "Takeout/Keep/note.json")
    assert draft is not None
    assert draft.meta["futureKey"] == 42
    assert draft.meta["anotherUnknown"] == "hello"


# ---------------------------------------------------------------------------
# parse: error handling
# ---------------------------------------------------------------------------


def test_malformed_member_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Corrupt JSON member logs a warning and is skipped; good member still yields."""
    good_note = _minimal_note()
    members = {
        "Takeout/Keep/corrupt.json": b"not { valid } json !!!",
        "Takeout/Keep/good.json": json.dumps(good_note).encode(),
    }
    zip_path = write_archive(tmp_path / "test.zip", members, "zip")

    discover()  # ensure plugin is registered

    archive = open_archive(zip_path)
    with caplog.at_level(logging.WARNING, logger="potluck.ingest.sources.google_keep"):
        drafts = list(parse(archive))

    assert len(drafts) == 1
    assert any(
        "corrupt" in record.message.lower()
        or "malformed" in record.message.lower()
        or "skipping" in record.message.lower()
        for record in caplog.records
    ), (
        f"Expected a warning about skipped malformed member; got: {[r.message for r in caplog.records]}"
    )


def test_non_dict_member_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Non-dict JSON root (e.g. array) logs a warning and is skipped; good member still yields."""
    good_note = _minimal_note()
    members = {
        "Takeout/Keep/array.json": b"[1, 2, 3]",
        "Takeout/Keep/good.json": json.dumps(good_note).encode(),
    }
    zip_path = write_archive(tmp_path / "test.zip", members, "zip")

    discover()  # ensure plugin is registered

    archive = open_archive(zip_path)
    with caplog.at_level(logging.WARNING, logger="potluck.ingest.sources.google_keep"):
        drafts = list(parse(archive))

    assert len(drafts) == 1
    assert any(
        "array" in record.message.lower()
        or "non-dict" in record.message.lower()
        or "skipping" in record.message.lower()
        for record in caplog.records
    ), f"Expected a warning about non-dict member; got: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["zip", "tgz", "dir"])
def test_detection(tmp_path: Path, fmt: str) -> None:
    """detect_source finds google_keep plugin for each supported archive format."""
    archive_path = write_keep_takeout(tmp_path / fmt, count=5, seed=1, fmt=fmt)  # type: ignore[arg-type]
    archive = open_archive(archive_path)
    plugin = detect_source(archive)
    assert plugin is not None, f"No plugin detected for fmt={fmt}"
    assert plugin.name == "google_keep"


def test_multipart_tgz_detection_and_full_parse(tmp_path: Path) -> None:
    """Multi-part tgz: open_archive on part1 → detect + parse yields all non-skipped notes."""
    count = 20
    seed = 77
    part1 = write_keep_takeout(tmp_path, count=count, seed=seed, fmt="tgz", parts=2)

    # Detection works via multi-part
    archive = open_archive(part1)
    plugin = detect_source(archive)
    assert plugin is not None
    assert plugin.name == "google_keep"

    # Full parse: reuse the implementation's own skip predicate
    non_skipped = sum(
        1 for n in synthetic_keep_notes(count, seed=seed) if _to_draft(n, "dummy.json") is not None
    )

    archive2 = open_archive(part1)
    drafts = list(plugin.parse(archive2))
    assert len(drafts) == non_skipped


# ---------------------------------------------------------------------------
# Golden fixture test
# ---------------------------------------------------------------------------

# Regenerate fixture:
#   python -c "
#   from pathlib import Path
#   from potluck.testing.keep import write_keep_takeout
#   write_keep_takeout(Path('tests/fixtures/keep'), 12, seed=7, fmt='dir')
#   "
# Expected counts for seed=7, count=12: 11 non-skipped notes (1 trashed, 0 empty).

_GOLDEN_ITEMS_NEW = 11  # exact count for seed=7, count=12


def test_golden_fixture(ctx: AppContext) -> None:
    """Import the committed dir fixture and verify exact item counts + spot values.

    Spot-checked values (seed=7, count=12 — stable unless fixture is regenerated):
    - "Ember Walnut Hazel" note has exact title and text
    - Checklist note at 2020-01-01T00:46 has correct markdown rendering
    - Re-import produces 0 new items (dedup works)
    """
    fixture = REPO_ROOT / "tests" / "fixtures" / "keep" / "takeout-synth-001"

    # The fixture is committed; a missing one is a repo defect, not a skip.
    assert fixture.exists(), (
        "committed golden fixture missing; regenerate with:\n"
        '  python -c "from pathlib import Path; from potluck.testing.keep import '
        "write_keep_takeout; write_keep_takeout(Path('tests/fixtures/keep'), 12, seed=7, fmt='dir')\""
    )

    run = import_path(ctx, fixture)

    assert run.status == "completed"
    assert run.source == "google_keep"
    assert run.items_new == _GOLDEN_ITEMS_NEW
    assert run.items_duplicate == 0

    # Spot-check 1: note with title "Ember Walnut Hazel"; its userEditedTimestampUsec
    # is 0, so ts falls back to createdTimestampUsec (1577837640000000 µs).
    with ctx.db.read() as conn:
        row = conn.execute(
            "SELECT title, text, ts FROM items WHERE title = ?",
            ("Ember Walnut Hazel",),
        ).fetchone()
    assert row is not None, "Expected 'Ember Walnut Hazel' in items"
    assert row[1] == "Violet cedar cedar indigo dune rowan maple."
    assert row[2] == "2020-01-01T00:14:00+00:00"

    # Spot-check 2: checklist note renders as markdown
    with ctx.db.read() as conn:
        row2 = conn.execute(
            "SELECT text FROM items WHERE external_id = ?",
            ("Takeout/Keep/2020-01-01T00_46_00.000+00_00.json",),
        ).fetchone()
    assert row2 is not None, "Expected checklist note by external_id"
    assert row2[0].startswith("- [ ] ochre walnut valley")

    # Spot-check 3: total in DB
    with ctx.db.read() as conn:
        total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert total == _GOLDEN_ITEMS_NEW

    # Spot-check 4: re-import produces 0 new (dedup)
    run2 = import_path(ctx, fixture)
    assert run2.items_new == 0
    assert run2.items_duplicate == _GOLDEN_ITEMS_NEW
