"""Tests for potluck.services.imports: import_path and list_imports."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from potluck.ingest.readers import Archive
from potluck.models.drafts import NoteDraft
from potluck.models.items import ItemKind
from potluck.services.context import AppContext
from potluck.testing.archives import write_archive

# ---------------------------------------------------------------------------
# Registry isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Isolate the plugin registry; restored by monkeypatch on teardown.

    Also patches potluck.ingest.sources.__path__ to an empty list so that
    detect_source's internal discover() call cannot import real source modules
    (e.g. google_keep) and accidentally pollute the registry.
    Toy plugins are registered directly into the fresh registry via @source,
    so end-to-end tests continue to work.
    """
    import potluck.ingest.plugins as plugins_mod
    import potluck.ingest.sources as sources_pkg

    fresh: dict[str, Any] = {}
    monkeypatch.setattr(plugins_mod, "_registry", fresh)
    monkeypatch.setattr(sources_pkg, "__path__", [])
    return fresh


# ---------------------------------------------------------------------------
# Toy parse function used by end-to-end test
# ---------------------------------------------------------------------------


def _toy_parse(archive: Archive) -> Iterator[NoteDraft]:
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

    run = import_path(ctx, zip_path)

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


def test_import_path_unsupported_archive(ctx: AppContext, tmp_path: Path) -> None:
    from potluck.core.errors import UnsupportedArchiveError
    from potluck.services.imports import import_path

    # A plain .txt file is not a supported archive format
    txt_path = tmp_path / "notes.txt"
    txt_path.write_text("not an archive")

    with pytest.raises(UnsupportedArchiveError):
        import_path(ctx, txt_path)


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

    runs = list_imports(ctx)
    assert len(runs) == 2
    # Newest first (b was imported second)
    assert runs[0].source == "list_src_b"
    assert runs[1].source == "list_src_a"

    # Limit works
    limited = list_imports(ctx, limit=1)
    assert len(limited) == 1
    assert limited[0].source == "list_src_b"
