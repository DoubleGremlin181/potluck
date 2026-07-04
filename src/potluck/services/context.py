"""AppContext: the shared runtime handle every service function takes."""

import logging
from dataclasses import dataclass, field

from potluck.core.config import Settings
from potluck.services.import_manager import ImportManager
from potluck.storage import imports as _storage_imports
from potluck.storage.db import Database

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppContext:
    """Configuration plus the open database, passed to every service call."""

    settings: Settings
    db: Database
    # Owns the background import worker (#132): one manager — and therefore
    # at most one running import — per context (one context per process).
    import_manager: ImportManager = field(default_factory=ImportManager)


def create_context(settings: Settings | None = None) -> AppContext:
    """Build the runtime context: load settings (unless given) and open the database."""
    resolved = settings if settings is not None else Settings()
    db = Database.open(resolved.db_path)
    # Startup recovery (#132): runs left 'running' by a crash/kill can never
    # resume — mark them failed('interrupted') before anything can observe
    # phantom progress. Counters keep the last committed batch's values.
    interrupted = db.write(_storage_imports.fail_stale_running_imports)
    if interrupted:
        _logger.warning("marked %d interrupted import run(s) as failed", interrupted)
    return AppContext(settings=resolved, db=db)
