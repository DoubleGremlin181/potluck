"""AppContext: the shared runtime handle every service function takes."""

from dataclasses import dataclass, field

from potluck.core.config import Settings
from potluck.services.import_manager import ImportManager
from potluck.storage.db import Database


@dataclass(frozen=True)
class AppContext:
    """Configuration plus the open database, passed to every service call."""

    settings: Settings
    db: Database
    # Owns the background import worker (#132): one manager — and therefore
    # at most one running import — per context (one context per process).
    import_manager: ImportManager = field(default_factory=ImportManager)


def create_context(settings: Settings | None = None) -> AppContext:
    """Build the runtime context: load settings (unless given) and open the database.

    Deliberately does NOT sweep stale 'running' import rows: a read-only
    invocation (status/search/show) must never mark another process's live
    import as interrupted. Recovery runs only where a process takes write
    ownership of the imports ledger — see
    ``services.imports.recover_interrupted_imports`` (API serve startup and
    the top of every import run).
    """
    resolved = settings if settings is not None else Settings()
    return AppContext(settings=resolved, db=Database.open(resolved.db_path))
