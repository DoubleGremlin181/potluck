"""Background import manager (#132): one import at a time, service-level.

Imports are the MVP's only user-visible long operation, so this stays
deliberately small: no jobs table, no queue — a single worker thread plus an
in-memory :class:`~potluck.models.imports.ImportTask` snapshot. Durable
progress lives on the imports row (the engine updates it once per committed
batch); the task exists so the whole operation — including archive detection,
which runs before any imports row exists — has a pollable state and a place
for detection-phase errors to land.

Writer-thread interplay: the worker thread only *submits* write closures via
``Database.write`` / ``write_async``, exactly like a CLI import — the single
writer thread still owns the sole write connection, so concurrent API reads
and writes stay safe. The worker is a daemon thread: process shutdown never
blocks behind a multi-hour import; the row it orphans is healed by startup
recovery (``fail_stale_running_imports``) on the next open.
"""

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from potluck.core.errors import ImportInProgressError
from potluck.models.imports import ImportRun, ImportStatus, ImportTask


class ImportManager:
    """Owns the single background import worker; at most one import runs at a time.

    One instance per AppContext. All state transitions happen under the lock;
    callers only ever see deep-copied snapshots.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._task: ImportTask | None = None

    def start(self, run: Callable[[], list[ImportRun]], *, path: str) -> ImportTask:
        """Run *run* on a fresh worker thread; return the initial task snapshot.

        Raises:
            ImportInProgressError: if an import is still running — the caller
                maps this to HTTP 409.
        """
        with self._lock:
            if self._task is not None and self._task.status == "running":
                raise ImportInProgressError(
                    f"an import of '{self._task.path}' is already running; "
                    "only one import runs at a time"
                )
            self._task = ImportTask(path=path, status="running", started_at=datetime.now(UTC))
            self._thread = threading.Thread(
                target=self._run, args=(run,), name="potluck-import", daemon=True
            )
            self._thread.start()
            return self._task.model_copy(deep=True)

    def _run(self, run: Callable[[], list[ImportRun]]) -> None:
        # BaseException: the task MUST reach a terminal state no matter what
        # escapes the run, or the conflict check would block all future
        # imports until restart. The engine has already recorded per-run
        # ledger failures; this only settles the in-memory handle.
        try:
            runs = run()
        except BaseException as exc:
            self._finish(status="failed", error=str(exc) or type(exc).__name__)
        else:
            self._finish(status="completed", import_ids=[r.id for r in runs])

    def _finish(
        self,
        *,
        status: ImportStatus,
        error: str | None = None,
        import_ids: list[int] | None = None,
    ) -> None:
        with self._lock:
            assert self._task is not None
            self._task = self._task.model_copy(
                update={
                    "status": status,
                    "error": error,
                    "import_ids": import_ids or [],
                    "finished_at": datetime.now(UTC),
                }
            )

    def status(self) -> ImportTask | None:
        """Snapshot of the current/last import operation; None before any start."""
        with self._lock:
            return self._task.model_copy(deep=True) if self._task is not None else None

    def join(self, timeout: float | None = None) -> None:
        """Wait for the worker thread to exit (test/shutdown hygiene only)."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
