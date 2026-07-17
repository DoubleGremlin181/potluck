"""Drive-pull end-to-end (#152 acceptance): list → download → ingest → record
(→ prune), against MockDrive serving a REAL 2-part synthetic Keep Takeout set.

Live puller + watcher threads at shortened intervals, real imports through
the background manager, real gdrive_pulls rows — the composition the serve
lifespan wires (decision doc §4), minus only the network (httpx.MockTransport;
no network in tests, ever — and no real-Drive validation is possible in CI).
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from potluck.core.paths import gdrive_token_path
from potluck.ingest.gdrive import DRIVE_SCOPE_FULL, DRIVE_SCOPE_READONLY, save_token
from potluck.models.gdrive import StoredToken
from potluck.services import gdrive as gdrive_service
from potluck.services import imports as imports_service
from potluck.services import watch as watch_service
from potluck.services.context import AppContext
from potluck.storage import gdrive_pulls as storage_gdrive_pulls
from potluck.testing.keep import write_keep_takeout
from tests.conftest import MockDrive

_DEADLINE_S = 30.0


def _wait_for(predicate: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + _DEADLINE_S
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def _seeded_mock(tmp_path: Path) -> tuple[MockDrive, list[str], list[str]]:
    """MockDrive with a Takeout folder holding a REAL 2-part Keep zip set."""
    write_keep_takeout(tmp_path / "gen", 6, seed=5, fmt="zip", parts=2)
    parts = sorted((tmp_path / "gen").glob("*.zip"))
    assert len(parts) == 2
    mock = MockDrive()
    folder = mock.add_folder("Takeout")
    ids = [mock.add_file(folder, p.name, p.read_bytes()) for p in parts]
    return mock, ids, [p.name for p in parts]


def _gdrive_ctx(ctx: AppContext, tmp_path: Path, *, prune: bool = False) -> AppContext:
    return AppContext(
        settings=ctx.settings.model_copy(
            update={
                "gdrive_client_id": "cid-1",
                "gdrive_client_secret": "csecret-1",
                "gdrive_downloads_dir": tmp_path / "gdrive-downloads",
                "gdrive_interval_s": 0.05,
                "gdrive_prune": prune,
                "watch_interval_s": 0.05,
            }
        ),
        db=ctx.db,
    )


def _stop_all(gctx: AppContext) -> None:
    gctx.puller.stop()
    gctx.watcher.stop()
    gctx.puller.join(_DEADLINE_S)
    gctx.watcher.join(_DEADLINE_S)
    gctx.import_manager.join(_DEADLINE_S)


def _completed_keep_runs(gctx: AppContext) -> list[str]:
    return [
        r.path
        for r in imports_service.list_imports(gctx).runs
        if r.status == "completed" and r.source == "google_keep"
    ]


def test_pull_ingest_record_end_to_end(ctx: AppContext, tmp_path: Path) -> None:
    """The acceptance flow: the puller lists and downloads the set into the
    watched downloads dir; the #151 watcher imports it; gdrive_pulls records
    every part — no API/CLI involvement, no network."""
    mock, ids, names = _seeded_mock(tmp_path)
    save_token(
        gdrive_token_path(),
        StoredToken(
            refresh_token="rtok-1",
            client_id="cid-1",
            scopes=[DRIVE_SCOPE_READONLY],
            obtained_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    gctx = _gdrive_ctx(ctx, tmp_path)
    assert gdrive_service.start_puller(gctx, transport=mock.transport()) is True
    # No watch_folders configured: the gdrive downloads dir alone powers this.
    assert watch_service.start_watcher(gctx) is True
    try:
        _wait_for(lambda: len(_completed_keep_runs(gctx)) > 0, "auto-import of the pulled set")

        # Ingested: the import ran over the downloaded representative part.
        [run_path] = _completed_keep_runs(gctx)
        downloads = gctx.settings.gdrive_downloads_dir
        assert run_path == str(downloads / names[0])
        [run] = [
            r
            for r in imports_service.list_imports(gctx).runs
            if r.status == "completed" and r.source == "google_keep"
        ]
        assert run.items_new == 6

        # Landed: final names only, no .part droppings.
        assert sorted(p.name for p in downloads.iterdir()) == names

        # Recorded: every Drive file id, with the set stem the watcher groups by.
        with gctx.db.read() as conn:
            assert storage_gdrive_pulls.filter_pulled(conn, ids) == set(ids)

        # Status surfaces the pull; readonly grant means no prune scope.
        status = gdrive_service.get_gdrive_status(gctx)
        assert status.auth_state == "ok"
        assert status.pulled_files == 2
        assert status.last_pull_at is not None
        assert status.last_error is None
        assert status.prune_scope_granted is False

        # Nothing was deleted remotely (prune is off — and unauthorized anyway).
        assert mock.deleted == []
    finally:
        _stop_all(gctx)


def test_prune_after_verified_import_end_to_end(ctx: AppContext, tmp_path: Path) -> None:
    """With gdrive_prune on AND the full scope granted, the puller deletes
    exactly the pulled ids — only after the set's import completed."""
    mock, ids, _names = _seeded_mock(tmp_path)
    save_token(
        gdrive_token_path(),
        StoredToken(
            refresh_token="rtok-1",
            client_id="cid-1",
            scopes=[DRIVE_SCOPE_READONLY, DRIVE_SCOPE_FULL],
            obtained_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    gctx = _gdrive_ctx(ctx, tmp_path, prune=True)
    assert gdrive_service.start_puller(gctx, transport=mock.transport()) is True
    assert watch_service.start_watcher(gctx) is True
    try:
        # Wait on the DURABLE terminal state — pruned_at stamped on every
        # row — never on the mock's deleted list: _prune issues files.delete
        # (which updates mock.deleted) BEFORE ops.mark_pruned queues its
        # write to the single writer thread, so a wait keyed on the mock can
        # win the race against the stamp and observe pruned_at=None rows
        # (the main-CI flake this replaced). Stamps are written after the
        # deletes on the same puller thread, so once they are visible every
        # assertion below is a deterministic consequence.
        def _all_stamped() -> bool:
            with gctx.db.read() as conn:
                row = conn.execute(
                    "SELECT count(*) FROM gdrive_pulls WHERE pruned_at IS NOT NULL"
                ).fetchone()
            return int(row[0]) == len(ids)

        _wait_for(_all_stamped, "prune stamps of the imported set")
        # Prune ran only after the import completed and was verified.
        assert len(_completed_keep_runs(gctx)) == 1
        assert sorted(mock.deleted) == sorted(ids)  # exact recorded ids, nothing else
        with gctx.db.read() as conn:
            assert storage_gdrive_pulls.list_prunable(conn) == []  # all stamped pruned
            assert storage_gdrive_pulls.count_pulls(conn) == 2  # history retained
    finally:
        _stop_all(gctx)
