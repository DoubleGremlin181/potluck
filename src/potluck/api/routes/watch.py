"""REST watch-folder endpoints (#151): status and the runtime enable toggle.

Thin adapter over ``services.watch`` — the poller itself is owned by the
serve lifespan; these handlers only read status and persist the toggle.
Deliberately absent from MCP (imports are, too): auto-import is an owner
concern, not an AI-client tool.
"""

from fastapi import APIRouter

from potluck.api.deps import CtxDep
from potluck.api.errors import error_responses
from potluck.models.watch import WatchStatus, WatchToggleRequest
from potluck.services import watch as watch_service

router = APIRouter(tags=["watch"])


@router.get("/watch", summary="Watch-folder status")
def watch_status(ctx: CtxDep) -> WatchStatus:
    """Watch-folder configuration and runtime: effective enabled flag (and
    whether config or a runtime toggle decides it), the configured folders
    with their existence, polling interval, last scan time, sets pending
    (stabilizing after a drop, or backing off after a failure), and the most
    recent auto-import error.

    Folders and interval are config-file-owned (`config.toml`); only the
    enabled flag is togglable at runtime via PATCH."""
    return watch_service.get_watch_status(ctx)


@router.patch(
    "/watch", summary="Enable or disable watch-folder auto-import", responses=error_responses(422)
)
def set_watch(ctx: CtxDep, req: WatchToggleRequest) -> WatchStatus:
    """Persist the runtime enable/disable toggle (survives restarts; wins
    over the config default) and return the resulting status. Takes effect
    on the watcher's next polling cycle — no restart involved."""
    return watch_service.set_watch_enabled(ctx, req.enabled)
