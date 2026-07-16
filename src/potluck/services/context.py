"""AppContext: the shared runtime handle every service function takes."""

from dataclasses import dataclass, field

from potluck.core.config import Settings
from potluck.services.gdrive_manager import DrivePuller
from potluck.services.import_manager import ImportManager
from potluck.services.watch_manager import FolderWatcher
from potluck.storage.db import Database


@dataclass(frozen=True)
class AppContext:
    """Configuration plus the open database, passed to every service call."""

    settings: Settings
    db: Database
    # Owns the background import worker (#132): one manager — and therefore
    # at most one running import — per context (one context per process).
    import_manager: ImportManager = field(default_factory=ImportManager)
    # Watch-folder poller (#151): exists on every context (status reads it),
    # but its thread is started only by the serve lifespan — CLI/one-shot
    # contexts never poll (write-ownership rule).
    watcher: FolderWatcher = field(default_factory=FolderWatcher)
    # Drive Takeout puller (#152): same ownership story as the watcher —
    # present everywhere for status, thread started only by the serve
    # lifespan; it only downloads (the watcher imports).
    puller: DrivePuller = field(default_factory=DrivePuller)


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
