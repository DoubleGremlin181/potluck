"""Detection cache (#196): same archive bytes + same registry => no re-scan.

On a real 3.8 GB Gmail tgz the no-op re-run was 76.7 s, 96% of it the
detection pass (tar must decompress fully to list names). Detection is a
pure function of (archive bytes, registered plugins' globs), so its outcome
is cached by (file_hash, registry fingerprint).
"""

import sqlite3
from pathlib import Path

import pytest

from potluck.core.errors import UnknownSourceError
from potluck.services.context import AppContext
from potluck.services.imports import import_path
from potluck.testing.archives import write_archive
from potluck.testing.mbox import write_gmail_takeout


def _detect_counter(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    from potluck.ingest import plugins as plugins_mod
    from potluck.services import imports as imports_mod

    calls: list[int] = []
    real = plugins_mod.detect_sources

    def counting(archive: object) -> object:
        calls.append(1)
        return real(archive)  # type: ignore[arg-type]

    monkeypatch.setattr(imports_mod, "detect_sources", counting)
    return calls


def test_archive_scans_table_exists(ctx: AppContext) -> None:
    with ctx.db.read() as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='archive_scans'"
        ).fetchone()
    assert row is not None
    assert "STRICT" in str(row[0])


def test_second_import_of_changed_content_skips_detection_only_if_same_bytes(
    ctx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _detect_counter(monkeypatch)
    archive = write_gmail_takeout(tmp_path / "takeout", 10, seed=3)

    import_path(ctx, archive)
    assert len(calls) == 1
    # Same bytes again: ledger short-circuits AND detection is not re-run.
    import_path(ctx, archive)
    assert len(calls) == 1

    # Different bytes: full detection again.
    larger = write_gmail_takeout(tmp_path / "larger", 15, seed=3)
    import_path(ctx, larger)
    assert len(calls) == 2


def test_renamed_copy_uses_cached_detection(
    ctx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _detect_counter(monkeypatch)
    archive = write_gmail_takeout(tmp_path / "takeout", 10, seed=3)
    import_path(ctx, archive)

    copy = tmp_path / "renamed.zip"
    copy.write_bytes(archive.read_bytes())
    import_path(ctx, copy)
    assert len(calls) == 1


def test_directory_archives_never_use_cache(
    ctx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _detect_counter(monkeypatch)
    root = write_gmail_takeout(tmp_path / "takeout", 5, seed=3, fmt="dir")
    import_path(ctx, root)
    import_path(ctx, root)
    assert len(calls) == 2


def test_registry_change_invalidates_cache(
    ctx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new registered plugin could match members the old scan ignored."""
    import dataclasses

    from potluck.ingest import plugins as plugins_mod

    calls = _detect_counter(monkeypatch)
    archive = write_gmail_takeout(tmp_path / "takeout", 5, seed=3)
    import_path(ctx, archive)
    assert len(calls) == 1

    # Register a synthetic extra plugin (changes the registry fingerprint).
    gmail = plugins_mod.discover()["gmail"]
    extra = dataclasses.replace(gmail, name="zz_extra", detect=plugins_mod.Glob("*Never/*.xyz"))
    monkeypatch.setitem(plugins_mod._registry, "zz_extra", extra)

    # Ledger short-circuit is keyed per source and still satisfied for gmail,
    # but detection must re-run under the new registry.
    import_path(ctx, archive)
    assert len(calls) == 2


def test_unknown_archive_detection_cached(
    ctx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A no-match scan is cached too: re-pointing at the same unknown archive
    fails fast without another full scan."""
    calls = _detect_counter(monkeypatch)
    unknown = write_archive(tmp_path / "u.zip", {"random/file.xyz": b"data"}, fmt="zip")

    with pytest.raises(UnknownSourceError):
        import_path(ctx, unknown)
    with pytest.raises(UnknownSourceError):
        import_path(ctx, unknown)
    assert len(calls) == 1


def test_cached_detection_produces_identical_runs(ctx: AppContext, tmp_path: Path) -> None:
    """End to end: cache hit path imports exactly like a fresh detection."""
    archive = write_gmail_takeout(tmp_path / "takeout", 8, seed=3)
    [run1] = import_path(ctx, archive)

    copy = tmp_path / "copy.zip"
    copy.write_bytes(archive.read_bytes())
    [run2] = import_path(ctx, copy)  # cache-hit detection + ledger short-circuit
    assert run2.id == run1.id

    def _scan_rows(conn: sqlite3.Connection) -> int:
        return int(conn.execute("SELECT COUNT(*) FROM archive_scans").fetchone()[0])

    with ctx.db.read() as conn:
        assert _scan_rows(conn) == 1
