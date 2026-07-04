"""System endpoints: liveness and database overview."""

from fastapi import APIRouter

from potluck import __version__
from potluck.api.deps import CtxDep
from potluck.models.stats import StatsResponse
from potluck.services.stats import get_stats

router = APIRouter(tags=["system"])


@router.get("/health", summary="Liveness check")
def health() -> dict[str, str]:
    """Report that the server is up and which app version is running."""
    return {"status": "ok", "version": __version__}


@router.get("/stats", summary="Database overview")
def stats(ctx: CtxDep) -> StatsResponse:
    """Counts of items, sources and imports, plus database location, size,
    and schema/app versions."""
    return get_stats(ctx)
