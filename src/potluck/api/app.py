"""FastAPI application factory: thin HTTP adapter over the service layer.

Endpoints live in ``api/routes/*`` as plain ``def`` functions (FastAPI runs
them on its threadpool), calling the same sync services the CLI and MCP
server use; routers reach the shared AppContext through ``app.state.context``
(see api/deps.py). Errors surface as the uniform envelope registered in
api/errors.py.

The MCP server rides along as the ``/mcp`` mount (#138): one server surface,
localhost-only with no auth (v1 threat model, #133) — the same tools stdio
clients get, over streamable HTTP on the serve port.
"""

import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from potluck import __version__
from potluck.api.errors import register_error_handlers
from potluck.api.routes import imports, items, search, system
from potluck.api.static import find_web_dist
from potluck.mcp.server import create_mcp
from potluck.services.context import AppContext, create_context
from potluck.services.imports import recover_interrupted_imports

_SPA_MISSING = (
    "Potluck API is running, but the SPA build was not found.\n"
    "Build it with: cd web && npm ci && npm run build\n"
    "API docs are at /api/docs\n"
)

# Bounded grace for a finishing background import at shutdown: clean exits
# settle the ledger row; a long-running import still exceeds this and leans
# on the next write-ownership sweep instead.
_SHUTDOWN_JOIN_S = 5.0


def create_app(ctx: AppContext | None = None, *, open_browser: bool = False) -> FastAPI:
    """Build the FastAPI app over an AppContext (created from config if omitted)."""
    context = ctx if ctx is not None else create_context()

    # path="/" + mount("/mcp") puts the MCP endpoint exactly at /mcp (clients
    # hitting the no-slash URL get Starlette's 307 to /mcp/, which MCP HTTP
    # clients follow).
    mcp_app = create_mcp(context).http_app(path="/")

    @asynccontextmanager
    async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
        # Serving = taking write ownership of the imports ledger (#132):
        # sweep stale 'running' rows before the first request can observe
        # phantom progress. ASGI lifespan startup completes before serving.
        recover_interrupted_imports(context)
        if open_browser:
            webbrowser.open(f"http://{context.settings.host}:{context.settings.port}/")
        # Starlette never runs a mounted sub-app's lifespan, and fastmcp's
        # owns the MCP session manager — compose it here or every /mcp
        # request dies with "task group is not initialized".
        async with mcp_app.lifespan(app_):
            yield
        # After MCP sessions wind down: bounded grace for a finishing import.
        context.import_manager.join(_SHUTDOWN_JOIN_S)

    app = FastAPI(
        title="Potluck",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.context = context
    register_error_handlers(app)
    app.include_router(system.router, prefix="/api")
    app.include_router(search.router, prefix="/api")
    app.include_router(items.router, prefix="/api")
    app.include_router(imports.router, prefix="/api")
    # Before the SPA catch-all: Starlette matches mounts in registration order.
    app.mount("/mcp", mcp_app)

    # Exact /mcp (the URL users configure): 307 preserves method and body, and
    # MCP HTTP clients follow it. Explicit because the SPA catch-all mount
    # would otherwise swallow /mcp before the router's redirect_slashes
    # fallback could fire.
    @app.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
    def mcp_no_slash() -> RedirectResponse:
        return RedirectResponse(url="/mcp/", status_code=307)

    web_dist = find_web_dist(context.settings)
    if web_dist is not None:
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="spa")
    else:

        @app.get("/", response_class=PlainTextResponse, include_in_schema=False)
        def spa_missing() -> str:
            return _SPA_MISSING

    return app
