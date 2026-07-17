"""Watch-folder service (#151): status, runtime toggle, watcher lifecycle.

The seam the API routes, CLI status and serve lifespan share. Configuration
(folder list, interval, the enabled DEFAULT) is config-file-owned; only the
enabled flag has a runtime override, persisted in the app_settings KV so the
settings-page toggle survives restarts. Effective enabled = KV value if
present, else ``settings.watch_enabled``.
"""

import logging
from collections.abc import Callable
from pathlib import Path

from potluck.models.imports import ImportRun
from potluck.models.watch import WatchEnabledSource, WatchFolder, WatchStatus
from potluck.services import imports as imports_service
from potluck.services.context import AppContext
from potluck.services.gdrive import gdrive_configured
from potluck.storage import app_settings as _storage_app_settings

_logger = logging.getLogger(__name__)

WATCH_ENABLED_KEY = "watch_enabled"


def effective_watch_folders(ctx: AppContext) -> tuple[Path, ...]:
    """Configured folders, plus the managed gdrive downloads dir when the
    Drive puller is configured (#152): the puller only downloads — this
    watcher debounces and imports what lands there (decision doc §4)."""
    folders = list(ctx.settings.watch_folders)
    gdrive_dir = ctx.settings.gdrive_downloads_dir
    if gdrive_configured(ctx.settings) and gdrive_dir not in folders:
        folders.append(gdrive_dir)
    return tuple(folders)


def effective_watch_enabled(ctx: AppContext) -> tuple[bool, WatchEnabledSource]:
    """(enabled, source): the persisted runtime toggle if set, else config."""
    with ctx.db.read() as conn:
        value = _storage_app_settings.get_setting(conn, WATCH_ENABLED_KEY)
    if isinstance(value, bool):
        return value, "runtime"
    return ctx.settings.watch_enabled, "config"


def get_watch_status(ctx: AppContext) -> WatchStatus:
    """Configuration plus this process's watcher runtime (GET /api/watch).

    Runtime fields (last_scan_at / pending / last_error) are meaningful only
    inside `potluck serve` — the sole process that polls. A CLI status call
    reports them empty, which is the truth for that process.
    """
    enabled, source = effective_watch_enabled(ctx)
    runtime = ctx.watcher.snapshot()
    return WatchStatus(
        enabled=enabled,
        effective_enabled_source=source,
        interval_s=ctx.settings.watch_interval_s,
        folders=[
            WatchFolder(path=str(folder), exists=folder.is_dir())
            for folder in effective_watch_folders(ctx)
        ],
        last_scan_at=runtime.last_scan_at,
        pending=runtime.pending,
        last_error=runtime.last_error,
    )


def set_watch_enabled(ctx: AppContext, enabled: bool) -> WatchStatus:
    """Persist the runtime toggle (PATCH /api/watch); return the new status.

    Takes effect on the watcher's next cycle — it re-reads the effective
    value every cycle, so no thread restart is involved.
    """
    ctx.db.write(lambda conn: _storage_app_settings.set_setting(conn, WATCH_ENABLED_KEY, enabled))
    return get_watch_status(ctx)


def start_watcher(ctx: AppContext) -> bool:
    """Configure and start the polling thread; False if nothing to watch.

    Serve-lifespan only (write-ownership rule: only the server may submit
    imports on a schedule). Started even when currently disabled — the
    runtime toggle can re-enable scanning without a restart; a disabled
    cycle costs one KV read.
    """
    folders = effective_watch_folders(ctx)
    if not folders:
        _logger.info("watch-folders: none configured; watcher not started")
        return False
    ctx.watcher.configure(
        folders=folders,
        interval_s=ctx.settings.watch_interval_s,
        enabled=lambda: effective_watch_enabled(ctx)[0],
        submit=lambda path, on_done: _submit_watch_import(ctx, path, on_done),
    )
    ctx.watcher.start()
    _logger.info(
        "watch-folders: polling %d folder(s) every %.0f s",
        len(folders),
        ctx.settings.watch_interval_s,
    )
    return True


def _submit_watch_import(
    ctx: AppContext, path: Path, on_done: Callable[[str | None], None]
) -> None:
    """Claim the import manager for *path* (raising ImportInProgressError when
    busy — the watcher's retry-next-cycle signal) and report the outcome to
    *on_done* when the background run settles.

    The completion callback exists because the manager's status() only tracks
    the LATEST task: a user-triggered import could replace the watcher's
    before it polls, silently losing the outcome. Wrapping the run closure
    observes it directly instead."""

    def run() -> list[ImportRun]:
        try:
            runs = imports_service.import_path(ctx, path)
        except BaseException as exc:
            on_done(str(exc) or type(exc).__name__)
            raise
        on_done(None)
        return runs

    ctx.import_manager.start(run, path=str(path))
