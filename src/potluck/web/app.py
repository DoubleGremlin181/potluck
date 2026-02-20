"""Web application for Potluck."""

import mimetypes
from pathlib import Path
from uuid import UUID

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from potluck.core.config import get_settings
from potluck.models.media import Media
from potluck.web.dependencies import SESSION_MAX_AGE, get_db, require_auth
from potluck.web.routers import (
    auth,
    dashboard,
    entity,
    events,
    imports,
    notes,
    people,
    search,
    settings,
    tags,
    timeline,
)
from potluck.web.routers import map as map_router
from potluck.web.routers import media as media_router

_WEB_DIR = Path(__file__).parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"


class AuthMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated requests to the login page.

    Skips auth check for /login, /static/*, and /favicon.ico.
    If WEB_PASSWORD is not set, all requests are allowed through.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        if not settings.web_password:
            return await call_next(request)

        path = request.url.path
        if path == "/login" or path.startswith(("/login/", "/static/")) or path == "/favicon.ico":
            return await call_next(request)

        token = request.cookies.get("session_token")
        if not token:
            return RedirectResponse(url="/login", status_code=303)

        serializer = URLSafeTimedSerializer(settings.web_secret_key)
        try:
            serializer.loads(token, max_age=SESSION_MAX_AGE)
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
    app.include_router(search.router)
    app.include_router(media_router.router)
    app.include_router(notes.router)
    app.include_router(people.router)
    app.include_router(timeline.router)
    app.include_router(imports.router)
    app.include_router(events.router)
    app.include_router(settings.router)
    app.include_router(map_router.router)
    app.include_router(tags.router)
    app.include_router(entity.router)

    async def _resolve_media_file(media_id: UUID, db: AsyncSession) -> tuple[Path, str]:
        """Look up a media file by database ID and return its path and content type.

        Raises HTTPException(404) if the media record or file is missing.
        """
        stmt = select(Media).where(col(Media.id) == media_id)
        result = await db.execute(stmt)
        media = result.scalar_one_or_none()

        if media is None or not media.file_path:
            raise HTTPException(status_code=404, detail="Media not found")

        file_path = Path(media.file_path)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found on disk")

        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        return file_path, content_type

    @app.get("/media/file/{media_id}", dependencies=[Depends(require_auth)])
    async def serve_media(media_id: UUID, db: AsyncSession = Depends(get_db)) -> FileResponse:
        """Serve a media file by its database ID.

        No direct filesystem paths are exposed to the client.
        """
        file_path, content_type = await _resolve_media_file(media_id, db)
        return FileResponse(path=file_path, media_type=content_type, filename=file_path.name)

    @app.get("/media/thumb/{media_id}", dependencies=[Depends(require_auth)])
    async def serve_thumbnail(media_id: UUID, db: AsyncSession = Depends(get_db)) -> FileResponse:
        """Serve a media file as a thumbnail (currently serves the original file)."""
        file_path, content_type = await _resolve_media_file(media_id, db)
        return FileResponse(path=file_path, media_type=content_type, filename=file_path.name)

    return app


def run_web_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Start the FastAPI web server with Uvicorn.

    Args:
        host: Host to bind to.
        port: Port to bind to.
    """
    app = create_app()
    uvicorn.run(app, host=host, port=port)
