"""Shared FastAPI dependencies for the web application."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from potluck.db.session import get_async_engine

# 30-day session cookie lifetime in seconds
SESSION_MAX_AGE = 86400 * 30


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session for FastAPI dependency injection."""
    engine = get_async_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
