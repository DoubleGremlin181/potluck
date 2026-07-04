"""Incremental ingestion (#126): superset deltas + ledger short-circuit."""

from pathlib import Path

from potluck.services.context import AppContext
from potluck.services.imports import import_path
from potluck.testing.mbox import write_gmail_takeout


def test_superset_reimport_ingests_only_delta(ctx: AppContext, tmp_path: Path) -> None:
    """A newer, larger Takeout adds only the new messages (prefix-stable
    generator: the first 100 messages of the 130 corpus are byte-identical)."""
    small = write_gmail_takeout(tmp_path / "small", 100, seed=3)
    [run1] = import_path(ctx, small)
    assert run1.items_new == 100

    large = write_gmail_takeout(tmp_path / "large", 130, seed=3)
    [run2] = import_path(ctx, large)
    assert run2.items_new == 30
    assert run2.items_duplicate == 100
    assert run2.items_updated == 0


def test_noop_rerun_short_circuits_on_file_hash(ctx: AppContext, tmp_path: Path) -> None:
    """Re-importing the SAME file skips parsing entirely: the completed ledger
    row for (source, file_hash, parser_version) is returned, no new row."""
    archive = write_gmail_takeout(tmp_path / "takeout", 50, seed=3)
    [run1] = import_path(ctx, archive)

    [run2] = import_path(ctx, archive)
    assert run2.id == run1.id, "expected the prior completed run, not a new one"

    with ctx.db.read() as conn:
        ledger_rows = conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
        items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert ledger_rows == 1
    assert items == 50


def test_renamed_copy_still_short_circuits(ctx: AppContext, tmp_path: Path) -> None:
    """The key is the content hash, not the path."""
    archive = write_gmail_takeout(tmp_path / "takeout", 20, seed=3)
    [run1] = import_path(ctx, archive)

    copy = tmp_path / "renamed.zip"
    copy.write_bytes(archive.read_bytes())
    [run2] = import_path(ctx, copy)
    assert run2.id == run1.id


def test_changed_file_runs_fully(ctx: AppContext, tmp_path: Path) -> None:
    small = write_gmail_takeout(tmp_path / "small", 20, seed=3)
    import_path(ctx, small)
    large = write_gmail_takeout(tmp_path / "large", 25, seed=3)
    [run2] = import_path(ctx, large)

    with ctx.db.read() as conn:
        ledger_rows = conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
    assert ledger_rows == 2
    assert run2.items_new == 5


def test_parser_version_bump_reingests(
    ctx: AppContext, tmp_path: Path, monkeypatch: object
) -> None:
    """parser_version is part of the short-circuit key: a parser upgrade must
    re-ingest even an unchanged file."""
    import dataclasses

    import pytest

    from potluck.ingest import plugins as plugins_mod

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    archive = write_gmail_takeout(tmp_path / "takeout", 10, seed=3)
    import_path(ctx, archive)

    registry = plugins_mod.discover()
    bumped = dataclasses.replace(registry["gmail"], parser_version=99)
    monkeypatch.setitem(plugins_mod._registry, "gmail", bumped)

    [run2] = import_path(ctx, archive)
    assert run2.parser_version == 99
    with ctx.db.read() as conn:
        ledger_rows = conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
    assert ledger_rows == 2


def test_directory_archive_never_short_circuits(ctx: AppContext, tmp_path: Path) -> None:
    """Directories have no file hash — every run is a full (dedup) run."""
    root = write_gmail_takeout(tmp_path / "takeout", 10, seed=3, fmt="dir")
    import_path(ctx, root)
    [run2] = import_path(ctx, root)
    assert run2.items_duplicate == 10
    with ctx.db.read() as conn:
        ledger_rows = conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
    assert ledger_rows == 2


def test_failed_run_does_not_short_circuit(ctx: AppContext, tmp_path: Path) -> None:
    """Only COMPLETED runs satisfy the short-circuit key."""
    archive = write_gmail_takeout(tmp_path / "takeout", 10, seed=3)
    [run1] = import_path(ctx, archive)
    ctx.db.write(
        lambda conn: conn.execute("UPDATE imports SET status = 'failed' WHERE id = ?", (run1.id,))
    )
    [run2] = import_path(ctx, archive)
    assert run2.id != run1.id
    assert run2.items_duplicate == 10


# ---------------------------------------------------------------------------
# parse-affecting settings in the short-circuit (#198 review 9)
# ---------------------------------------------------------------------------


def test_enabling_extraction_defeats_short_circuit(tmp_path: Path) -> None:
    """Import without extraction, enable extract_attachments, re-import: the
    run must re-parse and write blobs — 'same bytes + same parser' is not
    enough when settings change parse side effects."""
    from potluck.core.config import Settings
    from potluck.services.context import create_context

    archive = write_gmail_takeout(tmp_path / "takeout", 20, seed=7)
    db_path = tmp_path / "t.db"
    blobs = tmp_path / "blobs"

    ctx = create_context(Settings(db_path=db_path, extract_attachments=False))
    try:
        [run1] = import_path(ctx, archive)
    finally:
        ctx.db.close()

    ctx = create_context(Settings(db_path=db_path, extract_attachments=True, attachments_dir=blobs))
    try:
        [run2] = import_path(ctx, archive)
        assert run2.id != run1.id, "extraction toggle must defeat the short-circuit"
        blobs_written = [p for p in blobs.rglob("*") if p.is_file()]
        assert blobs_written, "expected extracted attachment blobs"

        # Same settings again: short-circuits against the extraction run.
        [run3] = import_path(ctx, archive)
        assert run3.id == run2.id
    finally:
        ctx.db.close()


def test_disabling_extraction_short_circuits_against_extraction_run(tmp_path: Path) -> None:
    """A prior extraction run covers a non-extraction request (superset rule):
    the blobs already exist, so nothing would change."""
    from potluck.core.config import Settings
    from potluck.services.context import create_context

    archive = write_gmail_takeout(tmp_path / "takeout", 20, seed=7)
    db_path = tmp_path / "t.db"

    ctx = create_context(
        Settings(db_path=db_path, extract_attachments=True, attachments_dir=tmp_path / "blobs")
    )
    try:
        [run1] = import_path(ctx, archive)
    finally:
        ctx.db.close()

    ctx = create_context(Settings(db_path=db_path, extract_attachments=False))
    try:
        [run2] = import_path(ctx, archive)
        assert run2.id == run1.id
    finally:
        ctx.db.close()
