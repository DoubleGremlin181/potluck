"""Database migration utilities."""

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from potluck.core.config import get_settings
from potluck.core.exceptions import DatabaseError
from potluck.core.logging import get_logger

logger = get_logger("db.migration")


def get_alembic_config() -> Config:
    """Get Alembic configuration."""
    config = Config("alembic.ini")
    settings = get_settings()
    config.set_main_option("sqlalchemy.url", settings.sync_database_url)
    return config


def get_current_revision() -> str | None:
    """Get the current database migration revision.

    Returns:
        Current revision hash, or None if no migrations applied.
    """
    settings = get_settings()
    engine = create_engine(settings.sync_database_url)

    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        return context.get_current_revision()


def get_head_revision() -> str | None:
    """Get the head (latest) migration revision.

    Returns:
        Head revision hash, or None if no migrations exist.
    """
    from alembic.script import ScriptDirectory

    config = get_alembic_config()
    script = ScriptDirectory.from_config(config)
    return script.get_current_head()


def check_database_migration() -> None:
    """Verify database is at the expected migration level.

    Raises:
        DatabaseError: If database is not at the expected migration level.
    """
    current = get_current_revision()
    head = get_head_revision()

    if head is None:
        logger.info("No migrations defined yet")
        return

    if current is None:
        raise DatabaseError(
            f"Database has no migrations applied. Expected: {head}. "
            "Run 'alembic upgrade head' to apply migrations."
        )

    if current != head:
        raise DatabaseError(
            f"Database migration mismatch. Current: {current}, Expected: {head}. "
            "Run 'alembic upgrade head' to update."
        )

    logger.info(f"Database migration verified: {current}")


def check_database_connection() -> None:
    """Verify database is accessible.

    Raises:
        DatabaseError: If database connection fails.
    """
    settings = get_settings()

    try:
        engine = create_engine(settings.sync_database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connection verified")
    except Exception as e:
        raise DatabaseError(f"Failed to connect to database: {e}") from e
