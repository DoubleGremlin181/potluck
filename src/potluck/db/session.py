"""Database session management."""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlmodel import Session

from potluck.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    """Get the SQLAlchemy engine instance.

    Uses sync database URL for Celery tasks and other sync code.
    The engine is cached and reused across calls.

    Note: @lru_cache is used for lazy initialization - the engine is only
    created on first use, after settings are configured. A module-level
    variable would be created at import time before settings are available.

    Returns:
        SQLAlchemy Engine instance.
    """
    settings = get_settings()
    return create_engine(
        settings.sync_database_url,
        echo=False,
        pool_pre_ping=True,
    )


def get_session() -> Generator[Session, None, None]:
    """Get a new database session.

    This is a generator function for use with FastAPI dependency injection.

    Yields:
        SQLModel Session instance.
    """
    engine = get_engine()
    with Session(engine) as session:
        yield session
