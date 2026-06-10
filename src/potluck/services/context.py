"""AppContext: the shared runtime handle every service function takes."""

from dataclasses import dataclass

from potluck.core.config import Settings
from potluck.storage.db import Database


@dataclass(frozen=True)
class AppContext:
    """Configuration plus the open database, passed to every service call."""

    settings: Settings
    db: Database


def create_context(settings: Settings | None = None) -> AppContext:
    """Build the runtime context: load settings (unless given) and open the database."""
    resolved = settings if settings is not None else Settings()
    return AppContext(settings=resolved, db=Database.open(resolved.db_path))
