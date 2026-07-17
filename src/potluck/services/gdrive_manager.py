"""Drive Takeout puller (#152): scheduled downloads into a watched folder.

One :class:`DrivePuller` per AppContext, mirroring ``watch_manager.py``'s
FolderWatcher exactly (configure/start/run_cycle/stop/join/snapshot, one
lock, a serve-lifespan-owned daemon thread — CLI/one-shot contexts never
poll). The puller ONLY downloads: new Takeout archive sets land in the
managed downloads dir, which is part of the #151 watcher's effective folder
list — the watcher debounces/claims/imports. The puller never touches
import_manager (decision doc §4).

Cycle behavior (docs/decisions/gdrive-takeout-pull.md §5/§6/§8):

- **pull**: resolve folders named ``folder_name``, list their archive
  children, group into sets with the same :func:`parse_part_name` rule the
  watcher uses, skip already-recorded ids (one batch query), then download
  each new set oldest-export-first. Parts stream to ``<name>.part``
  (invisible to the watcher) and the whole set is renamed together only
  after every member downloaded and md5-verified — set-atomic publish, so
  the watcher only ever sees whole sets.
- **prune** (default off, scope-gated): deletes exactly the recorded ids of
  verifiably imported sets, then stamps them pruned. A missing full-drive
  scope surfaces a re-auth instruction in status; it is never an implicit
  escalation and never a cycle failure.
- **failure modes**: transport errors (offline laptop) are a QUIET status
  fact — retried next cycle, never last_error, never backoff. Auth death
  (invalid_grant) latches ``reauth_required`` and stops Drive calls until
  the token file's fingerprint changes (`potluck gdrive auth` rewrote it).
  Anything else backs off 1, 2, 4 … capped cycles (cycles are ~a day at the
  shipped interval, so the cap is small — a stall must not outlive the
  2-month export cadence).
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from potluck.core.errors import GDriveAuthError
from potluck.ingest.gdrive import DRIVE_SCOPE_FULL, DriveClient, DriveFile
from potluck.ingest.readers import parse_part_name
from potluck.models.gdrive import GDrivePullRecord, GDriveRuntime

_logger = logging.getLogger(__name__)

# Same candidate set the watcher scans for — exactly what open_archive accepts.
_ARCHIVE_SUFFIXES = (".zip", ".tgz", ".tar.gz")

# In-flight download suffix: outside the watcher's suffix filter, so partial
# sets are never visible to auto-import.
_PART_SUFFIX = ".part"

# Backoff ceiling: failure n skips min(2**(n-1), 4) cycles. Cycles are a DAY
# at the shipped interval (vs the watcher's 10 s), so the cap stays small —
# the worst stall (4 days) is still well inside the 2-month export cadence.
_MAX_BACKOFF_CYCLES = 4


@dataclass(frozen=True)
class PullerOps:
    """The service-layer collaborators, injected so tests fake them.

    ``make_client`` returns None when the feature cannot run yet (client
    unconfigured, or no token — auth may happen while serving; the factory
    re-reads the token file every cycle, so a later `potluck gdrive auth`
    is picked up without a restart). ``token_fingerprint`` changes whenever
    the token file changes — the reauth latch key.
    """

    enabled: Callable[[], bool]
    make_client: Callable[[], DriveClient | None]
    token_fingerprint: Callable[[], object]
    filter_pulled: Callable[[list[str]], set[str]]
    record_pulls: Callable[[list[GDrivePullRecord]], None]
    prune_enabled: Callable[[], bool]
    list_prunable: Callable[[], list[GDrivePullRecord]]
    mark_pruned: Callable[[list[str]], None]


class DrivePuller:
    """Polls Drive for new Takeout archive sets and lands them for the watcher.

    One instance per AppContext. ``configure()`` then ``start()`` happen only
    in the serve lifespan; tests drive ``run_cycle()`` directly (synchronous,
    no thread). State transitions happen under the lock; ``snapshot()``
    returns DTO copies only. Downloads run OUTSIDE the lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._configured = False
        self._ops: PullerOps | None = None
        self._downloads_dir = Path(".")
        self._folder_name = "Takeout"
        self._interval_s = 86400.0  # placeholder; configure() binds the real value
        self._last_check_at: datetime | None = None
        self._last_pull_at: datetime | None = None
        self._offline = False
        self._reauth_required = False
        self._reauth_fingerprint: object | None = None
        self._failures = 0
        self._skip_remaining = 0
        self._last_error: str | None = None

    def configure(
        self,
        *,
        ops: PullerOps,
        downloads_dir: Path,
        folder_name: str,
        interval_s: float,
    ) -> None:
        """Bind the puller's collaborators (before ``start()``/``run_cycle()``)."""
        with self._lock:
            self._ops = ops
            self._downloads_dir = downloads_dir
            self._folder_name = folder_name
            self._interval_s = interval_s
            self._configured = True

    # -- one polling cycle -----------------------------------------------------

    def run_cycle(self) -> None:
        """One synchronous poll: pull new sets, prune eligible ones.

        Never raises: every failure mode maps onto snapshot state (the thread
        must survive anything, and tests assert the exact mapping).
        """
        ops = self._ops
        if ops is None or not ops.enabled():
            return  # disabled: skip entirely (cheap KV read only)
        with self._lock:
            if self._skip_remaining > 0:
                self._skip_remaining -= 1  # backoff: sit this cycle out
                return
            reauth_fingerprint = self._reauth_fingerprint
        if reauth_fingerprint is not None and ops.token_fingerprint() == reauth_fingerprint:
            return  # dead credential unchanged: no point burning requests
        now = datetime.now(UTC)
        try:
            client = ops.make_client()
            if client is None:
                # Unconfigured / not yet authorized: nothing to do this cycle
                # (the service reports WHY via auth_state).
                with self._lock:
                    self._last_check_at = now
                return
            with client:
                pulled = self._pull(ops, client)
                gate_error = self._prune(ops, client)
            with self._lock:
                self._last_check_at = now
                self._offline = False
                self._reauth_required = False
                self._reauth_fingerprint = None
                self._failures = 0
                self._skip_remaining = 0
                self._last_error = gate_error
                if pulled:
                    self._last_pull_at = now
        except GDriveAuthError as exc:
            # Only a re-auth recovers: latch until the token file changes.
            fingerprint = ops.token_fingerprint()
            with self._lock:
                self._last_check_at = now
                self._offline = False
                self._reauth_required = True
                self._reauth_fingerprint = fingerprint
                self._last_error = str(exc)
        except httpx.TransportError as exc:
            # Offline is NORMAL for a local-first app: a quiet status fact,
            # retried next cycle — never last_error, never backoff (§8).
            _logger.debug("gdrive unreachable, will retry next cycle: %s", exc)
            with self._lock:
                self._last_check_at = now
                self._offline = True
        except Exception as exc:  # GDriveApiError + surprises: backoff
            _logger.exception("gdrive pull cycle failed")
            with self._lock:
                self._last_check_at = now
                self._offline = False
                self._failures += 1
                self._skip_remaining = min(2 ** (self._failures - 1), _MAX_BACKOFF_CYCLES)
                self._last_error = str(exc) or type(exc).__name__

    # -- pull ---------------------------------------------------------------------

    def _pull(self, ops: PullerOps, client: DriveClient) -> bool:
        """Download every new archive set; True if anything was published."""
        self._downloads_dir.mkdir(parents=True, exist_ok=True)
        candidates: list[DriveFile] = []
        for folder in client.list_folders(self._folder_name):
            candidates.extend(client.list_children(folder.id))
        archives = [f for f in candidates if f.name.lower().endswith(_ARCHIVE_SUFFIXES)]
        if not archives:
            return False
        already = ops.filter_pulled([f.id for f in archives])  # ONE batch query
        sets: dict[str, list[DriveFile]] = {}
        for file in archives:
            if file.id in already:
                continue  # bandwidth-saving skip; the ledger guards correctness
            parsed = parse_part_name(file.name)
            stem = parsed[0] if parsed is not None else file.name
            sets.setdefault(stem, []).append(file)
        pulled_any = False
        # Timestamped stems sort chronologically: oldest export first.
        for stem in sorted(sets):
            members = sorted(sets[stem], key=lambda f: f.name)
            for file in members:
                final = self._downloads_dir / file.name
                if final.exists():
                    # Crash recovery: published earlier but never recorded —
                    # don't clobber what the watcher may be importing.
                    continue
                client.download(
                    file.id,
                    self._downloads_dir / (file.name + _PART_SUFFIX),
                    expected_md5=file.md5,
                )
            # Set-atomic publish: every member is on disk and verified — only
            # now do the renames make the set visible to the watcher.
            for file in members:
                part = self._downloads_dir / (file.name + _PART_SUFFIX)
                if part.exists():
                    part.replace(self._downloads_dir / file.name)
            # pulled_at is stamped AFTER the last rename (review I1): the
            # prune gate only trusts an import that STARTED after the set's
            # newest pulled_at, and that guarantee holds only if pulled_at
            # postdates the moment every part became visible.
            now = datetime.now(UTC)
            ops.record_pulls(
                [
                    GDrivePullRecord(
                        file_id=file.id,
                        name=file.name,
                        md5=file.md5,
                        set_stem=stem,
                        local_path=str(self._downloads_dir / file.name),
                        pulled_at=now,
                    )
                    for file in members
                ]
            )
            pulled_any = True
        return pulled_any

    # -- prune (default off, §6) -----------------------------------------------

    def _prune(self, ops: PullerOps, client: DriveClient) -> str | None:
        """Delete verifiably-imported pulled ids; returns the scope-gate
        message (a persistent status note, NOT a cycle failure) or None."""
        if not ops.prune_enabled():
            return None
        if not client.has_scope(DRIVE_SCOPE_FULL):
            return (
                "gdrive_prune is enabled but the full Drive scope was not "
                "granted — re-run `potluck gdrive auth --prune`"
            )
        prunable = ops.list_prunable()
        if not prunable:
            return None
        for record in prunable:
            client.delete(record.file_id)  # 404 = already gone: idempotent
        ops.mark_pruned([record.file_id for record in prunable])
        _logger.info("gdrive prune: deleted %d imported archive(s) from Drive", len(prunable))
        return None

    # -- status --------------------------------------------------------------

    def snapshot(self) -> GDriveRuntime:
        """Runtime snapshot for the status service (DTO copies only)."""
        with self._lock:
            return GDriveRuntime(
                last_check_at=self._last_check_at,
                last_pull_at=self._last_pull_at,
                offline=self._offline,
                reauth_required=self._reauth_required,
                backoff_cycles=self._skip_remaining if self._skip_remaining > 0 else None,
                last_error=self._last_error,
            )

    # -- thread wrapper (thin; run_cycle carries the logic) -------------------

    def start(self) -> None:
        """Spawn the daemon polling thread (serve lifespan only; idempotent)."""
        with self._lock:
            if not self._configured:
                raise RuntimeError("DrivePuller.start() before configure()")
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="potluck-gdrive", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                self.run_cycle()
            except Exception as exc:  # the poller must never die mid-serve
                _logger.exception("gdrive cycle failed")
                with self._lock:
                    self._last_error = str(exc) or type(exc).__name__
            if self._stop.wait(self._interval_s):
                return

    def stop(self) -> None:
        """Signal the polling thread to exit after its current cycle."""
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the polling thread to exit (shutdown/test hygiene only)."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def is_running(self) -> bool:
        """Whether the polling thread is alive (status/test seam)."""
        with self._lock:
            return self._thread is not None and self._thread.is_alive()
