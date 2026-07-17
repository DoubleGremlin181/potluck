"""Generic text/markdown notes source plugin (#150): titles, path identity,
mtime timestamps, decoding, textclean posture, the size cap, in-place updates.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from potluck.ingest.plugins import ParseContext, discover
from potluck.ingest.readers import open_archive
from potluck.ingest.sources.notes import MAX_NOTE_BYTES, parse
from potluck.models.drafts import NoteDraft
from potluck.services.context import AppContext
from potluck.services.imports import import_path
from potluck.testing.archives import write_archive

_ZIP_EPOCH = datetime(1980, 1, 1, tzinfo=UTC)  # write_archive pins zip date_time


def _drafts(
    tmp_path: Path,
    members: dict[str, bytes],
    fmt: Literal["zip", "tgz", "dir"] = "zip",
) -> list[NoteDraft]:
    dest = tmp_path / ("notes_dir" if fmt == "dir" else f"notes.{fmt}")
    archive_path = write_archive(dest, members, fmt)
    drafts = list(parse(open_archive(archive_path), ParseContext()))
    return [d for d in drafts if isinstance(d, NoteDraft)]  # narrows; parse yields only these


def _potluck_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.message for r in caplog.records if r.name.startswith("potluck")]


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------


def test_markdown_h1_becomes_title(tmp_path: Path) -> None:
    body = b"Some preamble.\n\n# Synthetic Plan\n\nDetails follow.\n"
    [draft] = _drafts(tmp_path, {"Notes/plan.md": body})
    assert draft.title == "Synthetic Plan"
    assert draft.text is not None and "Details follow." in draft.text
    assert "# Synthetic Plan" in draft.text  # content keeps the full file


def test_markdown_without_h1_uses_filename_stem(tmp_path: Path) -> None:
    [draft] = _drafts(tmp_path, {"Notes/no-heading.md": b"## only a subheading\nbody\n"})
    assert draft.title == "no-heading"


def test_txt_always_uses_filename_stem(tmp_path: Path) -> None:
    [draft] = _drafts(tmp_path, {"deep/nested/todo-list.txt": b"# looks like markdown\n"})
    assert draft.title == "todo-list"


def test_markdown_long_extension_variant(tmp_path: Path) -> None:
    [draft] = _drafts(tmp_path, {"guide.markdown": b"# Guide Title\nbody\n"})
    assert draft.title == "Guide Title"


def test_empty_h1_falls_back_to_stem(tmp_path: Path) -> None:
    [draft] = _drafts(tmp_path, {"blank-heading.md": b"#   \nbody\n"})
    assert draft.title == "blank-heading"


def test_h1_inside_code_fence_is_not_a_title(tmp_path: Path) -> None:
    """A shell comment inside a ``` fence must not win over the real H1 that
    follows it (task-9 review Minor 1)."""
    body = b"```bash\n# install deps\nmake install\n```\n\n# Real Title\nbody\n"
    [draft] = _drafts(tmp_path, {"setup.md": body})
    assert draft.title == "Real Title"


def test_fence_only_markdown_falls_back_to_stem(tmp_path: Path) -> None:
    [draft] = _drafts(tmp_path, {"snippet.md": b"```sh\n# just a comment\n```\n"})
    assert draft.title == "snippet"


def test_bare_dotfile_name_titles_as_full_filename(tmp_path: Path) -> None:
    """A member literally named ``.txt`` (editor dropping) has an empty stem —
    the full filename is the fallback, never an empty title (task-9 review
    Minor 2)."""
    [draft] = _drafts(tmp_path, {"Notes/.txt": b"stray editor dropping\n"})
    assert draft.title == ".txt"


# ---------------------------------------------------------------------------
# Identity / timestamps
# ---------------------------------------------------------------------------


def test_identity_is_relative_member_path(tmp_path: Path) -> None:
    [draft] = _drafts(tmp_path, {"Notes/journal/2024-03-01.md": b"# Day\n"})
    assert draft.external_id == "notes:Notes/journal/2024-03-01.md"
    assert draft.kind.value == "note"
    assert draft.meta == {}


def test_ts_from_member_mtime(tmp_path: Path) -> None:
    [draft] = _drafts(tmp_path, {"a.txt": b"hello\n"})
    assert draft.ts == _ZIP_EPOCH


def test_ts_none_when_no_usable_mtime(tmp_path: Path) -> None:
    [draft] = _drafts(tmp_path, {"a.txt": b"hello\n"}, fmt="tgz")
    assert draft.ts is None


def test_edited_note_updates_in_place(ctx: AppContext, tmp_path: Path) -> None:
    """Path identity means an edit is an UPDATE of the same logical item —
    the timeline re-inference posture; a rename would mint a new item."""
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "plan.md").write_bytes(b"# Plan\nfirst version\n")
    [run1] = import_path(ctx, folder)
    assert (run1.items_new, run1.items_updated) == (1, 0)

    (folder / "plan.md").write_bytes(b"# Plan\nsecond version, edited\n")
    [run2] = import_path(ctx, folder)
    assert (run2.items_new, run2.items_updated) == (0, 1)

    with ctx.db.read() as conn:
        rows = conn.execute("SELECT external_id, text FROM items").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "notes:plan.md"
    assert "second version" in rows[0][1]


# ---------------------------------------------------------------------------
# Decoding / cleanup / caps
# ---------------------------------------------------------------------------


def test_invalid_utf8_decodes_with_replacement(tmp_path: Path) -> None:
    [draft] = _drafts(tmp_path, {"latin.txt": b"caf\xe9 plans\n"})
    assert draft.text == "caf� plans\n"


def test_utf8_bom_is_stripped(tmp_path: Path) -> None:
    [draft] = _drafts(tmp_path, {"bom.md": b"\xef\xbb\xbf# BOM Title\n"})
    assert draft.title == "BOM Title"
    assert draft.text is not None and draft.text.startswith("# BOM Title")


def test_textclean_posture_applies(tmp_path: Path) -> None:
    """Zero-width junk is stripped and unbroken >=120-char runs truncate to
    80 — the established ingest-time cleanup (#199)."""
    body = ("zero​width " + "x" * 200 + "\n").encode()
    [draft] = _drafts(tmp_path, {"junk.txt": body})
    assert draft.text is not None
    assert "​" not in draft.text
    assert "zerowidth" in draft.text
    assert "x" * 80 in draft.text and "x" * 81 not in draft.text


def test_oversize_member_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    members = {
        "huge-trace.txt": b"x" * (MAX_NOTE_BYTES + 1),
        "small.txt": b"keep me\n",
    }
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(tmp_path, members)
    assert [d.title for d in drafts] == ["small"]
    warnings = _potluck_warnings(caplog)
    assert len(warnings) == 1
    assert "huge-trace.txt" in warnings[0] and "skipped" in warnings[0]


def test_empty_file_yields_titled_note(tmp_path: Path) -> None:
    """A 0-byte file still carries information in its name (unlike Keep,
    where an empty note has no name of its own)."""
    [draft] = _drafts(tmp_path, {"empty.txt": b""})
    assert draft.title == "empty"
    assert draft.text is None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_registered_as_generic_note_source() -> None:
    plugin = discover()["notes"]
    assert plugin.generic is True
    assert plugin.detect.matches("Notes/plan.md")
    assert plugin.detect.matches("todo.txt")
    assert plugin.detect.matches("deep/guide.markdown")
    assert not plugin.detect.matches("data.csv")
    assert not plugin.detect.matches("README")
    assert not plugin.detect.matches("photo.jpg")
