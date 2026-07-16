"""Watch service (#151): effective-enabled precedence, toggle persistence,
status assembly, and the serve-lifespan start seam."""

import time
from pathlib import Path

from potluck.services import watch as watch_service
from potluck.services.context import AppContext, create_context


def _watch_ctx(ctx: AppContext, tmp_path: Path, *folders: Path) -> AppContext:
    """The ctx fixture with watch folders configured (same db)."""
    return AppContext(
        settings=ctx.settings.model_copy(
            update={"watch_folders": list(folders), "watch_interval_s": 0.05}
        ),
        db=ctx.db,
    )


# ---------------------------------------------------------------------------
# Effective enabled: KV override if present, else the config default
# ---------------------------------------------------------------------------


def test_enabled_defaults_to_config_value(ctx: AppContext) -> None:
    status = watch_service.get_watch_status(ctx)
    assert status.enabled is True  # Settings.watch_enabled default
    assert status.effective_enabled_source == "config"


def test_config_disabled_respected_without_kv(ctx: AppContext) -> None:
    off = AppContext(settings=ctx.settings.model_copy(update={"watch_enabled": False}), db=ctx.db)
    status = watch_service.get_watch_status(off)
    assert status.enabled is False
    assert status.effective_enabled_source == "config"


def test_runtime_toggle_overrides_config(ctx: AppContext) -> None:
    status = watch_service.set_watch_enabled(ctx, False)
    assert status.enabled is False
    assert status.effective_enabled_source == "runtime"

    # The runtime value wins even against a config that says True …
    assert watch_service.get_watch_status(ctx).enabled is False

    # … and toggling back on is runtime-owned too.
    back = watch_service.set_watch_enabled(ctx, True)
    assert back.enabled is True
    assert back.effective_enabled_source == "runtime"


def test_runtime_toggle_persists_across_context_rebuild(ctx: AppContext) -> None:
    """The KV row lives in the database, not the process: a fresh context on
    the same file still sees the override."""
    watch_service.set_watch_enabled(ctx, False)

    rebuilt = create_context(ctx.settings)
    try:
        status = watch_service.get_watch_status(rebuilt)
        assert status.enabled is False
        assert status.effective_enabled_source == "runtime"
    finally:
        rebuilt.db.close()


# ---------------------------------------------------------------------------
# Status assembly
# ---------------------------------------------------------------------------


def test_status_reports_folders_with_existence(ctx: AppContext, tmp_path: Path) -> None:
    present = tmp_path / "present"
    present.mkdir()
    missing = tmp_path / "missing"
    wctx = _watch_ctx(ctx, tmp_path, present, missing)

    status = watch_service.get_watch_status(wctx)
    assert [(f.path, f.exists) for f in status.folders] == [
        (str(present), True),
        (str(missing), False),
    ]
    assert status.interval_s == 0.05
    # No watcher thread in this process: runtime fields are empty, not errors.
    assert status.last_scan_at is None
    assert status.pending == []
    assert status.last_error is None


# ---------------------------------------------------------------------------
# start_watcher: the serve-lifespan seam
# ---------------------------------------------------------------------------


def test_start_watcher_without_folders_is_a_noop(ctx: AppContext) -> None:
    assert watch_service.start_watcher(ctx) is False
    ctx.watcher.stop()
    ctx.watcher.join(1.0)


def test_start_watcher_polls_and_respects_runtime_toggle(ctx: AppContext, tmp_path: Path) -> None:
    """A started watcher scans (last_scan_at advances) and a runtime disable
    stops scanning without stopping the thread."""
    folder = tmp_path / "watched"
    folder.mkdir()
    wctx = _watch_ctx(ctx, tmp_path, folder)

    assert watch_service.start_watcher(wctx) is True
    try:
        deadline = time.monotonic() + 30.0
        while watch_service.get_watch_status(wctx).last_scan_at is None:
            assert time.monotonic() < deadline, "watcher never scanned"
            time.sleep(0.01)

        # Disable at runtime: scans stop (the thread keeps ticking, idle).
        watch_service.set_watch_enabled(wctx, False)
        time.sleep(0.2)  # several intervals
        frozen = watch_service.get_watch_status(wctx).last_scan_at
        time.sleep(0.2)
        assert watch_service.get_watch_status(wctx).last_scan_at == frozen
    finally:
        wctx.watcher.stop()
        wctx.watcher.join(5.0)
