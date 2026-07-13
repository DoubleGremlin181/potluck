"""Tests for potluck.services.imports: import_path and list_imports."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from potluck.ingest.plugins import ParseContext
from potluck.ingest.readers import Archive
from potluck.models.drafts import NoteDraft
from potluck.models.items import ItemKind
from potluck.services.context import AppContext
from potluck.testing.archives import write_archive

# ---------------------------------------------------------------------------
# Toy parse function used by end-to-end test
# ---------------------------------------------------------------------------


def _toy_parse(archive: Archive, ctx: ParseContext) -> Iterator[NoteDraft]:
    """Yields exactly 2 NoteDrafts from any *Toy/*.txt member."""
    for _member, stream in archive.iter_members("*Toy/*.txt"):
        content = stream.read().decode()
        yield NoteDraft(title="note1", text=f"toyplugincontent: {content}")
        yield NoteDraft(title="note2", text=f"toyplugincontent: {content} (second)")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_import_path_end_to_end(
    ctx: AppContext, tmp_path: Path, clean_registry: dict[str, Any]
) -> None:
    from potluck.ingest.plugins import Glob, source
    from potluck.services.imports import import_path

    # Register a toy plugin that matches *Toy/*.txt
    source(name="toy_src", detect=Glob("*Toy/*.txt"), kinds=(ItemKind.NOTE,))(_toy_parse)

    # Build a zip with one member matching the glob
    zip_path = write_archive(
        tmp_path / "takeout.zip",
        {"Takeout/Toy/x.txt": b"hello toy"},
        fmt="zip",
    )

    [run] = import_path(ctx, zip_path)

    assert run.status == "completed"
    assert run.source == "toy_src"
    assert run.items_new == 2
    assert run.items_duplicate == 0
    assert run.file_hash is not None
    assert len(run.file_hash) == 64  # sha256 hex = 64 chars

    # Items must be indexed in FTS
    with ctx.db.read() as conn:
        rows = conn.execute(
            "SELECT rowid FROM items_fts WHERE items_fts MATCH ?",
            ("toyplugincontent",),
        ).fetchall()
    assert len(rows) == 2, f"Expected 2 FTS-indexed items, found {len(rows)}"


def test_import_path_unknown_source(
    ctx: AppContext, tmp_path: Path, clean_registry: dict[str, Any]
) -> None:
    from potluck.core.errors import UnknownSourceError
    from potluck.services.imports import import_path

    # Zip with content that no registered plugin recognises
    zip_path = write_archive(
        tmp_path / "unknown.zip",
        {"some/random/file.xyz": b"data"},
        fmt="zip",
    )

    with pytest.raises(UnknownSourceError):
        import_path(ctx, zip_path)


def test_import_path_unrecognized_plain_file(ctx: AppContext, tmp_path: Path) -> None:
    """A plain file opens as a single-member archive (#148 — bare exports
    like Timeline.json are real import shapes), so an unrecognizable one
    fails as UnknownSourceError, not as an unsupported format."""
    from potluck.core.errors import UnknownSourceError
    from potluck.services.imports import import_path

    txt_path = tmp_path / "notes.txt"
    txt_path.write_text("not an export any plugin recognises")

    with pytest.raises(UnknownSourceError):
        import_path(ctx, txt_path)


def test_import_path_corrupt_zip_raises_potluck_error(ctx: AppContext, tmp_path: Path) -> None:
    """A corrupt zip surfaces as UnsupportedArchiveError (a PotluckError the
    interface layers handle), not a raw zipfile.BadZipFile."""
    from potluck.core.errors import UnsupportedArchiveError
    from potluck.services.imports import import_path

    bad = tmp_path / "corrupt.zip"
    bad.write_bytes(b"PK\x03\x04" + b"\x00" * 32)

    with pytest.raises(UnsupportedArchiveError, match="corrupt or unreadable"):
        import_path(ctx, bad)


def test_import_path_corrupt_tgz_raises_potluck_error(ctx: AppContext, tmp_path: Path) -> None:
    from potluck.core.errors import UnsupportedArchiveError
    from potluck.services.imports import import_path

    bad = tmp_path / "corrupt.tgz"
    bad.write_bytes(b"\x1f\x8b" + b"\x00" * 32)

    with pytest.raises(UnsupportedArchiveError, match="corrupt or unreadable"):
        import_path(ctx, bad)


def test_list_imports(ctx: AppContext, tmp_path: Path, clean_registry: dict[str, Any]) -> None:
    from potluck.ingest.plugins import Glob, source
    from potluck.services.imports import import_path, list_imports

    source(name="list_src_a", detect=Glob("*A/*.txt"), kinds=(ItemKind.NOTE,))(_toy_parse)
    source(name="list_src_b", detect=Glob("*B/*.txt"), kinds=(ItemKind.NOTE,))(_toy_parse)

    zip_a = write_archive(
        tmp_path / "a.zip",
        {"Takeout/A/x.txt": b"aaa"},
        fmt="zip",
    )
    zip_b = write_archive(
        tmp_path / "b.zip",
        {"Takeout/B/x.txt": b"bbb"},
        fmt="zip",
    )

    import_path(ctx, zip_a)
    import_path(ctx, zip_b)

    history = list_imports(ctx)
    assert history.total == 2
    assert len(history.runs) == 2
    # Newest first (b was imported second)
    assert history.runs[0].source == "list_src_b"
    assert history.runs[1].source == "list_src_a"

    # Limit pages, total stays the unpaginated count
    limited = list_imports(ctx, limit=1)
    assert limited.total == 2
    assert len(limited.runs) == 1
    assert limited.runs[0].source == "list_src_b"


# ---------------------------------------------------------------------------
# #195: combined archives import every matching source
# ---------------------------------------------------------------------------


def test_combined_takeout_imports_all_products(ctx: AppContext, tmp_path: Path) -> None:
    """One zip holding Keep AND Mail yields one ImportRun per source, with
    per-source ledger rows and both item sets present (real plugins)."""
    import json as _json

    from potluck.services.imports import import_path
    from potluck.testing.keep import synthetic_keep_notes
    from potluck.testing.mbox import MBOX_MEMBER, synthetic_mbox_messages

    note = next(iter(synthetic_keep_notes(1, seed=5)))
    members = {
        "Takeout/Keep/note1.json": _json.dumps(note).encode(),
        MBOX_MEMBER: b"".join(synthetic_mbox_messages(3, seed=5)),
    }
    zip_path = write_archive(tmp_path / "combined.zip", members, fmt="zip")

    runs = import_path(ctx, zip_path)

    assert [r.source for r in runs] == ["gmail", "google_keep"]
    assert all(r.status == "completed" for r in runs)
    by_source = {r.source: r for r in runs}
    assert by_source["gmail"].items_new == 3
    assert by_source["google_keep"].items_new == 1

    with ctx.db.read() as conn:
        kinds = {
            str(r[0]): int(r[1])
            for r in conn.execute("SELECT kind, COUNT(*) FROM items GROUP BY kind").fetchall()
        }
        ledger = conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
    assert kinds == {"email": 3, "note": 1}
    assert ledger == 2


def test_single_source_archive_returns_one_run(ctx: AppContext, tmp_path: Path) -> None:
    from potluck.services.imports import import_path
    from potluck.testing.mbox import write_gmail_takeout

    archive_path = write_gmail_takeout(tmp_path / "takeout", 4, seed=5)
    runs = import_path(ctx, archive_path)
    assert len(runs) == 1
    assert runs[0].source == "gmail"
    assert runs[0].items_new == 4


# Synthetic reproducer for the real-data defect: undecodable raw header bytes
# surrogateescape into header strings; one such From display name crashed a
# 126k-email import in content_hash. The whole message must ingest instead.
_SURROGATE_BEARING_MESSAGE = (
    b"From bad@potluck.test Fri Dec 12 06:57:49 +0000 2025\n"
    b"Message-ID: <surrogate-fixture@potluck.test>\n"
    b'From: "\x93Bad Name\x94" <bad@potluck.test>\n'
    b"To: ok@potluck.test\n"
    b"Subject: junk \x93quoted\x94 subject\n"
    b"Date: Fri, 12 Dec 2025 06:57:49 +0000\n"
    b"X-Gmail-Labels: Inbox,\x93Junk\x94\n"
    b"Content-Type: text/plain; charset=UTF-8\n"
    b"\n"
    b"surrogate fixture body\n"
)


def test_import_survives_undecodable_header_bytes(ctx: AppContext, tmp_path: Path) -> None:
    """End-to-end: a message whose headers surrogateescape must still land in
    items + emails (content_hash and the SQLite TEXT binds both need clean
    UTF-8) and must never fail the run."""
    from potluck.services.imports import import_path
    from potluck.testing.mbox import MBOX_MEMBER, synthetic_mbox_messages

    mbox = b"".join(synthetic_mbox_messages(2, seed=5)) + _SURROGATE_BEARING_MESSAGE
    zip_path = write_archive(tmp_path / "takeout.zip", {MBOX_MEMBER: mbox}, fmt="zip")

    [run] = import_path(ctx, zip_path)

    assert run.status == "completed"
    assert run.source == "gmail"
    assert run.items_new == 3  # the surrogate-bearing message is NOT skipped

    with ctx.db.read() as conn:
        row = conn.execute(
            """SELECT e.from_name, i.title FROM emails e JOIN items i ON i.id = e.item_id
               WHERE e.message_id = 'surrogate-fixture@potluck.test'"""
        ).fetchone()
    assert row is not None
    from_name, title = row
    from_name.encode("utf-8")  # stored value is clean UTF-8
    assert "Bad Name" in from_name  # readable characters survive the scrub
    assert "subject" in title
