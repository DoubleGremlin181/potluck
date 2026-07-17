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
from fastapi.responses import HTMLResponse, RedirectResponse

from potluck import __version__
from potluck.api.errors import register_error_handlers
from potluck.api.routes import gdrive, imports, items, search, system, watch
from potluck.api.static import SPAStaticFiles, find_web_dist
from potluck.mcp.server import create_mcp
from potluck.services.context import AppContext, create_context
from potluck.services.gdrive import start_puller
from potluck.services.imports import recover_interrupted_imports
from potluck.services.watch import start_watcher

# Served at "/" when no SPA build exists — the normal state of a
# source install (`uvx --from git+…`), whose wheel is built on the user's
# machine where web/dist never exists (#141). Everything but the web app
# works, so the page says exactly that and routes people to the API docs, the
# MCP endpoint, and the two ways to get the full web app.
_SPA_MISSING_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Potluck — web app not bundled</title>
    <style>
      body { font-family: system-ui, sans-serif; max-width: 40rem;
             margin: 4rem auto; padding: 0 1rem; line-height: 1.6; }
      code { background: #8881; border-radius: 4px; padding: 0.1rem 0.3rem; }
    </style>
  </head>
  <body>
    <h1>Potluck is running</h1>
    <p>The REST API and MCP server are up — but this install has no web app
    build. Installs from source (<code>uvx --from git+https://…</code>) don't
    ship it.</p>
    <ul>
      <li><a href="/api/docs">Interactive API docs</a></li>
      <li>MCP endpoint for AI clients: <code>/mcp</code></li>
    </ul>
    <p>To get the web app, install from a
    <a href="https://github.com/DoubleGremlin181/potluck/releases">release</a>
    instead — release wheels and the Docker image embed it. Working from a
    checkout? Build it with <code>cd web &amp;&amp; npm ci &amp;&amp; npm run
    build</code> and restart.</p>
  </body>
</html>
"""

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
        # The finally also covers a startup failure AFTER the poller threads
        # start (MCP lifespan enter, webbrowser.open): they must never
        # outlive a failed startup. stop()/join() are safe on never-started
        # managers.
        try:
            # Same ownership moment starts the watch-folder poller (#151):
            # only the serving process may submit imports on a schedule.
            # No-op when no folders are configured (the gdrive downloads dir
            # counts as one once a Drive client is configured).
            start_watcher(context)
            # And the Drive Takeout puller (#152): downloads-only — what it
            # lands in the (watched) downloads dir, the watcher above
            # imports. No-op when no OAuth client is configured.
            start_puller(context)
            if open_browser:
                webbrowser.open(f"http://{context.settings.host}:{context.settings.port}/")
            # Starlette never runs a mounted sub-app's lifespan, and
            # fastmcp's owns the MCP session manager — compose it here or
            # every /mcp request dies with "task group is not initialized".
            async with mcp_app.lifespan(app_):
                yield
        finally:
            # Puller first (stop landing new archives), watcher next (it
            # must not claim new imports while we drain), then bounded grace
            # for a finishing import.
            context.puller.stop()
            context.watcher.stop()
            context.puller.join(_SHUTDOWN_JOIN_S)
            context.watcher.join(_SHUTDOWN_JOIN_S)
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
    app.include_router(watch.router, prefix="/api")
    app.include_router(gdrive.router, prefix="/api")
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
        # html=True serves index.html at "/"; the subclass adds the SPA
        # fallback so client-route deep links survive hard reloads (#135).
        app.mount("/", SPAStaticFiles(directory=web_dist, html=True), name="spa")
    else:

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def spa_missing() -> str:
            return _SPA_MISSING_HTML

    return app
