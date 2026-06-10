"""FastAPI application: thin HTTP adapter over the service layer.

Endpoints are plain ``def`` functions (FastAPI runs them on its threadpool),
calling the same sync services the CLI and MCP server use.
"""

import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from potluck import __version__
from potluck.api.static import find_web_dist
from potluck.models.stats import StatsResponse
from potluck.services.context import AppContext, create_context
from potluck.services.stats import get_stats

_SPA_MISSING = (
    "Potluck API is running, but the SPA build was not found.\n"
    "Build it with: cd web && npm ci && npm run build\n"
    "API docs are at /api/docs\n"
)


def create_app(ctx: AppContext | None = None, *, open_browser: bool = False) -> FastAPI:
    """Build the FastAPI app over an AppContext (created from config if omitted)."""
    context = ctx if ctx is not None else create_context()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if open_browser:
            webbrowser.open(f"http://{context.settings.host}:{context.settings.port}/")
        yield

    app = FastAPI(
        title="Potluck",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/stats")
    def stats() -> StatsResponse:
        return get_stats(context)

    web_dist = find_web_dist(context.settings)
    if web_dist is not None:
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="spa")
    else:

        @app.get("/", response_class=PlainTextResponse, include_in_schema=False)
        def spa_missing() -> str:
            return _SPA_MISSING

    return app
