"""Shared FastAPI dependencies for the web application."""

from collections.abc import AsyncGenerator

from fastapi import Cookie, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from potluck.core.config import Settings, get_settings
from potluck.db.session import get_async_engine


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session for FastAPI dependency injection."""
    engine = get_async_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


def get_current_settings() -> Settings:
    """Return the application settings."""
    return get_settings()


async def require_auth(
    request: Request,
    session_token: str | None = Cookie(default=None),
) -> None:
    """Verify the user is authenticated via signed cookie.

    Skips verification if WEB_PASSWORD is not configured.
    Raises HTTPException(303) to redirect to login if not authenticated.
    """
    settings = get_settings()
    if not settings.web_password:
        return

    if not session_token:
        raise HTTPException(status_code=303, headers={"Location": "/login"})

    serializer = URLSafeTimedSerializer(settings.web_secret_key)
    try:
        serializer.loads(session_token, max_age=86400 * 30)  # 30 days
    except BadSignature as exc:
        raise HTTPException(status_code=303, headers={"Location": "/login"}) from exc
