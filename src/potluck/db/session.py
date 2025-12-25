"""Database session management."""

from collections.abc import Generator
from functools import lru_cache
from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine

from potluck.core.config import get_settings

if TYPE_CHECKING:
    from sqlmodel import Session


@lru_cache
def get_engine() -> Engine:
    """Get the SQLAlchemy engine instance.

    Uses sync database URL for Celery tasks and other sync code.
    The engine is cached and reused across calls.

    Returns:
        SQLAlchemy Engine instance.
    """
    settings = get_settings()
    return create_engine(
        settings.sync_database_url,
        echo=False,
        pool_pre_ping=True,
    )


def get_session() -> Generator["Session", None, None]:
    """Get a new database session.

    This is a generator function for use with FastAPI dependency injection.

    Yields:
        SQLModel Session instance.
    """
    from sqlmodel import Session

    engine = get_engine()
    with Session(engine) as session:
        yield session
