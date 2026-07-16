"""REST Drive-pull endpoints (#152): status and the runtime enable toggle.

Thin adapter over ``services.gdrive`` — the puller itself is owned by the
serve lifespan; these handlers only read status and persist the toggle. A
parallel route (not folded into /api/watch): the status DTOs share no
fields beyond the toggle pattern. Deliberately absent from MCP (like watch
and imports): auto-pull is an owner concern, not an AI-client tool. The
one-time authorization flow is CLI-only (`potluck gdrive auth`) — it needs a
browser plus a loopback listener on the server's host (decision doc §2).
"""

from fastapi import APIRouter

from potluck.api.deps import CtxDep
from potluck.api.errors import error_responses
from potluck.models.gdrive import GDriveStatus, GDriveToggleRequest
from potluck.services import gdrive as gdrive_service

router = APIRouter(tags=["gdrive"])


@router.get("/gdrive", summary="Google Drive Takeout auto-pull status")
def gdrive_status(ctx: CtxDep) -> GDriveStatus:
    """Drive-pull configuration and runtime: whether an OAuth client is
    configured, the auth state (unconfigured / unauthorized / ok /
    reauth_required), the effective enabled flag and what decides it, prune
    posture and whether its scope was granted, the Takeout folder name,
    interval, downloads dir, pull counters and the most recent error.

    Client credentials, folder, interval and prune are config-file-owned
    (`config.toml`); only the enabled flag is togglable at runtime via PATCH.
    Authorization itself is CLI-only: `potluck gdrive auth`."""
    return gdrive_service.get_gdrive_status(ctx)


@router.patch(
    "/gdrive",
    summary="Enable or disable Drive Takeout auto-pull",
    responses=error_responses(422),
)
def set_gdrive(ctx: CtxDep, req: GDriveToggleRequest) -> GDriveStatus:
    """Persist the runtime enable/disable toggle (survives restarts; wins
    over the config default) and return the resulting status. Takes effect
    on the puller's next polling cycle — no restart involved."""
    return gdrive_service.set_gdrive_enabled(ctx, req.enabled)
