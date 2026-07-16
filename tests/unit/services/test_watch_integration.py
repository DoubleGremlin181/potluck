"""Watch-folder end-to-end (#151): real tmp dirs, running watcher thread,
shortened interval, real imports through the background manager.

Acceptance arithmetic (the issue's "import starts < 30 s", asserted
structurally — never by sleeping 30 s): the two-scan debounce claims a set on
the SECOND consecutive scan seeing an unchanged fingerprint, i.e. within
2 polling intervals of an atomic drop and within 1 interval of the debounce
confirming stability. At the default 30 s interval that is a < 30 s react
after debounce (and ≤ 60 s from the drop itself — the confirming scan IS the
debounce). These tests run the identical code path at a 0.05 s interval, so
the observed react is tens of milliseconds; the deadline-bounded polls below
would fail long before 30 s if the watcher missed a cycle.
"""

import shutil
import time
from collections.abc import Callable
from pathlib import Path

from potluck.services import imports as imports_service
from potluck.services import watch as watch_service
from potluck.services.context import AppContext
from potluck.testing.keep import write_keep_takeout

_DEADLINE_S = 30.0


def _wait_for(predicate: Callable[[], bool], what: str) -> None:
    """Poll *predicate* until true or the deadline expires (no blind sleeps)."""
    deadline = time.monotonic() + _DEADLINE_S
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def _completed_runs(ctx: AppContext) -> list[str]:
    return [r.source for r in imports_service.list_imports(ctx).runs if r.status == "completed"]


def _start_watching(ctx: AppContext, folder: Path) -> AppContext:
    wctx = AppContext(
        settings=ctx.settings.model_copy(
            update={"watch_folders": [folder], "watch_interval_s": 0.05}
        ),
        db=ctx.db,
    )
    assert watch_service.start_watcher(wctx) is True
    return wctx


def _stop_watching(ctx: AppContext) -> None:
    ctx.watcher.stop()
    ctx.watcher.join(_DEADLINE_S)
    ctx.import_manager.join(_DEADLINE_S)


def test_dropped_archive_auto_imports(ctx: AppContext, tmp_path: Path) -> None:
    """Drop a generated Keep zip into a watched dir under a live watcher →
    the import runs to completion without any API/CLI involvement."""
    folder = tmp_path / "watched"
    folder.mkdir()
    archive = write_keep_takeout(tmp_path / "gen", 5, seed=3, fmt="zip")

    wctx = _start_watching(ctx, folder)
    try:
        shutil.copy2(archive, folder / archive.name)
        _wait_for(lambda: "google_keep" in _completed_runs(wctx), "auto-import to complete")

        [run] = [r for r in imports_service.list_imports(wctx).runs if r.status == "completed"]
        assert run.source == "google_keep"
        assert run.items_new == 5
        assert run.path == str(folder / archive.name)

        # The set is imported and quiet: nothing pending, no error.
        status = watch_service.get_watch_status(wctx)
        assert status.pending == []
        assert status.last_error is None
    finally:
        _stop_watching(wctx)

    # Ledger short-circuit keeps repeats cheap: the watcher never resubmits an
    # unchanged set, and even a manual re-import of the same bytes no-ops.
    [rerun] = imports_service.import_path(ctx, folder / archive.name)
    assert rerun.id == run.id  # the prior completed run, returned verbatim


def test_corrupt_drop_backs_off_then_fixed_file_imports(ctx: AppContext, tmp_path: Path) -> None:
    """Partial/corrupt drops retried gracefully (the acceptance criterion):
    a corrupt zip fails detection → backoff with the error surfaced; the
    fixed re-drop resets the state and imports immediately."""
    folder = tmp_path / "watched"
    folder.mkdir()
    fixed = write_keep_takeout(tmp_path / "gen", 4, seed=9, fmt="zip")

    wctx = _start_watching(ctx, folder)
    try:
        # A corrupt archive: valid zip magic, garbage after — detection fails
        # before any ledger row exists, so the failure lands on the watcher.
        (folder / "takeout-broken.zip").write_bytes(b"PK\x03\x04" + b"\x00" * 64)

        def _failed_with_backoff() -> bool:
            status = watch_service.get_watch_status(wctx)
            return status.last_error is not None and any(
                p.state == "backoff" for p in status.pending
            )

        _wait_for(_failed_with_backoff, "corrupt drop to fail and enter backoff")
        status = watch_service.get_watch_status(wctx)
        assert status.last_error is not None and "corrupt" in status.last_error

        # The fix: drop real bytes under the same name. Fingerprint change =
        # fresh attempt (backoff wiped), and the import completes.
        shutil.copy2(fixed, folder / "takeout-broken.zip")
        _wait_for(lambda: "google_keep" in _completed_runs(wctx), "fixed drop to import")
        assert watch_service.get_watch_status(wctx).pending == []
    finally:
        _stop_watching(wctx)


def test_two_drops_import_sequentially(ctx: AppContext, tmp_path: Path) -> None:
    """Two sets stable in the same cycle: one claims the manager, the other
    rides the claim-busy retry to completion on a later cycle."""
    folder = tmp_path / "watched"
    folder.mkdir()
    first = write_keep_takeout(tmp_path / "gen-a", 3, seed=1, fmt="zip")
    second = write_keep_takeout(tmp_path / "gen-b", 6, seed=2, fmt="zip")

    wctx = _start_watching(ctx, folder)
    try:
        shutil.copy2(first, folder / "export-a.zip")
        shutil.copy2(second, folder / "export-b.zip")
        _wait_for(
            lambda: len([s for s in _completed_runs(wctx) if s == "google_keep"]) == 2,
            "both drops to import",
        )
        runs = imports_service.list_imports(wctx).runs
        assert {r.path for r in runs if r.status == "completed"} == {
            str(folder / "export-a.zip"),
            str(folder / "export-b.zip"),
        }
        assert {r.items_new for r in runs if r.status == "completed"} == {3, 6}
    finally:
        _stop_watching(wctx)
