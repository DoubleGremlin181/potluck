"""Web application for Potluck."""

from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from potluck.core.config import get_settings
from potluck.web.routers import auth, dashboard

_WEB_DIR = Path(__file__).parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"


class AuthMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated requests to the login page.

    Skips auth check for /login, /static, and /favicon.ico.
    If WEB_PASSWORD is not set, all requests are allowed through.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        if not settings.web_password:
            return await call_next(request)

        path = request.url.path
        if path.startswith(("/login", "/static", "/favicon.ico")):
            return await call_next(request)

        token = request.cookies.get("session_token")
        if not token:
            return RedirectResponse(url="/login", status_code=303)

        serializer = URLSafeTimedSerializer(settings.web_secret_key)
        try:
            serializer.loads(token, max_age=86400 * 30)
        except BadSignature:
            return RedirectResponse(url="/login", status_code=303)

        return await call_next(request)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Potluck",
        description="Personal Knowledge Database",
        docs_url=None,
        redoc_url=None,
    )

    # Jinja2 templates
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["basename"] = lambda path: Path(path).name if path else ""
    app.state.templates = templates

    # Static files
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Auth middleware
    app.add_middleware(AuthMiddleware)

    # Routers
    app.include_router(auth.router)
    app.include_router(dashboard.router)

    return app


def run_web_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Start the FastAPI web server with Uvicorn.

    Args:
        host: Host to bind to.
        port: Port to bind to.
    """
    app = create_app()
    uvicorn.run(app, host=host, port=port)
