"""Watch-folder poller (#151): debounced auto-import of dropped archives.

One :class:`FolderWatcher` per AppContext (mirroring ``import_manager.py``'s
ownership style); its daemon thread is started ONLY by the serve lifespan —
CLI/one-shot contexts never poll, because submitting imports means taking
write ownership of the imports ledger, and that belongs to the server.

The state machine, per archive *set* (a multi-part drop grouped by
:func:`~potluck.ingest.readers.parse_part_name`, or a single file):

- **debounce**: a set is claimed only when every present member's
  fingerprint (size, mtime_ns) is unchanged across two consecutive scans —
  a file still being copied (or a multi-part set still arriving) changes
  every scan and is never claimed mid-copy. A part arriving between scans
  changes the SET fingerprint, restarting the debounce; a part arriving
  after the set already imported re-imports it once the set is quiet again
  (item-level dedup absorbs the overlap — rare, documented tradeoff).
- **claim**: submission goes through the import manager's atomic claim; a
  claim-busy (something else is importing) changes NO state — the set simply
  retries next cycle.
- **backoff**: an import FAILURE parks the set for 1, 2, 4, … up to 32
  cycles (per consecutive failure). Any fingerprint change (the user
  re-drops a fixed file) wipes the backoff for a fresh immediate attempt.
  In-memory only — a restart means at most one extra retry, and the
  content-hash ledger makes re-imports of already-imported bytes no-ops.
- **imported**: a successfully imported fingerprint is never resubmitted;
  repeats are cheap anyway (ledger short-circuit) but resubmitting would
  churn the imports page for nothing.

Reaction time: an atomic drop is first seen within one interval and claimed
one interval later — within 2 cycles of the drop, i.e. ≤ 20 s at the shipped
10 s default. That meets #151's acceptance ("import starts < 30 s") and the
#98 budget under their plain reading, with margin.

``last_error`` reports the most recent failed auto-import and clears itself
when the set that produced it recovers — a later successful import of that
set, or a re-drop (fingerprint change) invalidating the recorded failure.
Another set's success never masks it.

Non-goals (documented decisions, not oversights): inotify/watchdog (the
issue mandates stdlib polling), bare non-archive files (manual import covers
them; possible follow-up), folder-list mutation at runtime (config-file
owned), watching ``uploads_dir`` (already managed by the upload endpoint).
"""

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from potluck.core.errors import ImportInProgressError
from potluck.ingest.readers import parse_part_name
from potluck.models.watch import WatchPendingSet, WatchRuntime

_logger = logging.getLogger(__name__)

# Candidate extensions — exactly what open_archive accepts for archive files.
_ARCHIVE_SUFFIXES = (".zip", ".tgz", ".tar.gz")

# Backoff ceiling: failure n skips min(2**(n-1), 32) cycles (~5 min at the
# default 10 s interval) — a corrupt drop retries a few times then goes
# quiet, visible in status, without hammering the import pipeline.
_MAX_BACKOFF_CYCLES = 32

# submit(representative_path, on_done): claims the import manager (raising
# ImportInProgressError when busy) and later calls on_done(None) on success
# or on_done(error_text) on failure, from the import worker thread.
SubmitFn = Callable[[Path, Callable[[str | None], None]], None]

# A set's fingerprint: member file name -> (size, mtime_ns). Any member
# growing/touched/added/removed changes the mapping.
_Fingerprint = dict[str, tuple[int, int]]

# Set key: (folder, stem, ext) for multi-part names, (folder, file name)
# for plain archives — sets never group across folders.
_SetKey = tuple[str, ...]


@dataclass
class _SetState:
    """Mutable per-set bookkeeping (guarded by the watcher lock)."""

    fingerprint: _Fingerprint
    representative: Path
    stable_scans: int = 1  # consecutive scans observing this exact fingerprint
    inflight: bool = False  # submitted; the import worker owns it right now
    imported: bool = False  # this exact fingerprint already imported fine
    failures: int = 0  # consecutive failures (backoff exponent)
    skip_remaining: int = 0  # cycles left to sit out (backoff)


@dataclass
class _Scan:
    """One folder sweep: the sets present right now."""

    sets: dict[_SetKey, tuple[Path, _Fingerprint]] = field(default_factory=dict)


class FolderWatcher:
    """Polls configured folders and auto-imports stable archive drops.

    One instance per AppContext. ``configure()`` then ``start()`` happen only
    in the serve lifespan; tests drive ``run_cycle()`` directly (synchronous,
    no thread). All state transitions happen under the lock; ``snapshot()``
    returns DTO copies only.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._configured = False
        self._folders: tuple[Path, ...] = ()
        self._interval_s = 10.0  # placeholder; configure() binds the real value
        self._enabled: Callable[[], bool] = lambda: False
        self._submit: SubmitFn = _submit_unconfigured
        self._states: dict[_SetKey, _SetState] = {}
        self._missing_warned: set[Path] = set()
        self._last_scan_at: datetime | None = None
        # The most recent failure and WHICH set produced it — the key lets a
        # later recovery of that same set clear the error (review I2). None
        # key = a set-unattributable cycle error (only a new error replaces it).
        self._last_error: str | None = None
        self._last_error_key: _SetKey | None = None

    def configure(
        self,
        *,
        folders: tuple[Path, ...],
        interval_s: float,
        enabled: Callable[[], bool],
        submit: SubmitFn,
    ) -> None:
        """Bind the watcher's collaborators (before ``start()``/``run_cycle()``).

        ``enabled`` is re-read every cycle (the runtime toggle takes effect
        without a restart); ``submit`` performs the import-manager claim.
        """
        with self._lock:
            self._folders = folders
            self._interval_s = interval_s
            self._enabled = enabled
            self._submit = submit
            self._configured = True

    # -- one polling cycle --------------------------------------------------

    def run_cycle(self) -> None:
        """One synchronous poll: scan, debounce, claim. Never raises for
        per-folder/per-file trouble (missing folders, vanished files)."""
        if not self._enabled():
            return  # disabled: skip the scan entirely (cheap KV read only)
        scan = self._scan_folders()
        submits: list[tuple[_SetKey, Path, _Fingerprint]] = []
        with self._lock:
            # Vanished sets (user deleted/moved the files) drop their state.
            for key in [k for k in self._states if k not in scan.sets]:
                del self._states[key]

            for key in sorted(scan.sets):
                representative, fingerprint = scan.sets[key]
                state = self._states.get(key)
                if state is None:
                    # First sight: scan 1 of the two-scan debounce.
                    self._states[key] = _SetState(
                        fingerprint=fingerprint, representative=representative
                    )
                    continue
                state.representative = representative
                if fingerprint != state.fingerprint:
                    # Still changing (mid-copy / part arriving / re-drop):
                    # restart the debounce and treat it as a fresh attempt —
                    # a fixed file must not inherit the corrupt file's backoff,
                    # and the re-drop invalidates its recorded failure too.
                    state.fingerprint = fingerprint
                    state.stable_scans = 1
                    state.imported = False
                    state.failures = 0
                    state.skip_remaining = 0
                    self._clear_error_for(key)
                    continue
                state.stable_scans += 1
                if state.inflight or state.imported:
                    continue
                if state.skip_remaining > 0:
                    state.skip_remaining -= 1  # backoff: sit this cycle out
                    continue
                if state.stable_scans >= 2:
                    # inflight is set BEFORE the claim: a very fast import
                    # could settle before submit() even returns, and on_done
                    # must never race a later inflight=True.
                    state.inflight = True
                    submits.append((key, representative, dict(fingerprint)))
            self._last_scan_at = datetime.now(UTC)

        for key, representative, fingerprint in submits:
            try:
                self._submit(representative, self._on_done_callback(key, fingerprint))
            except ImportInProgressError:
                # Claim-busy: something else is importing. No state change —
                # the set stays eligible and simply retries next cycle.
                with self._lock:
                    state = self._states.get(key)
                    if state is not None:
                        state.inflight = False
            except Exception as exc:
                # Unexpected claim failure: never leave the set stuck
                # inflight; surface the error and let later cycles retry.
                _logger.exception("watch submit failed for %s", representative)
                with self._lock:
                    state = self._states.get(key)
                    if state is not None:
                        state.inflight = False
                    self._last_error = f"{representative}: {exc}"
                    self._last_error_key = key

    def _clear_error_for(self, key: _SetKey) -> None:
        """Drop last_error if *key* is the set that produced it (lock held)."""
        if self._last_error_key == key:
            self._last_error = None
            self._last_error_key = None

    def _scan_folders(self) -> _Scan:
        """Group the folders' archive files into sets with fingerprints."""
        scan = _Scan()
        # Per multi-part set: numeric order -> path, to pick the first part.
        orders: dict[_SetKey, list[tuple[tuple[int, int], str, Path]]] = {}
        for folder in self._folders:
            try:
                with os.scandir(folder) as scandir_it:
                    entries = sorted(scandir_it, key=lambda e: e.name)
            except OSError:
                # Missing (or unreadable) folder: warned once, never fatal —
                # status keeps reporting exists=False until it appears.
                if folder not in self._missing_warned:
                    self._missing_warned.add(folder)
                    _logger.warning("watch folder missing or unreadable: %s", folder)
                continue
            self._missing_warned.discard(folder)
            for entry in entries:
                name = entry.name
                if not name.lower().endswith(_ARCHIVE_SUFFIXES):
                    continue
                try:
                    if not entry.is_file():
                        continue
                    stat = entry.stat()
                except OSError:
                    continue  # vanished mid-scan: absent this cycle
                fingerprint = (stat.st_size, stat.st_mtime_ns)
                parsed = parse_part_name(name)
                if parsed is None:
                    key: _SetKey = (str(folder), name)
                    scan.sets[key] = (Path(entry.path), {name: fingerprint})
                else:
                    stem, ext, order = parsed
                    key = (str(folder), stem, ext)
                    orders.setdefault(key, []).append((order, name, Path(entry.path)))
                    _, members = scan.sets.get(key, (Path(entry.path), {}))
                    members[name] = fingerprint
                    scan.sets[key] = (Path(entry.path), members)
        # Multi-part representative: the numerically first part (ties broken
        # by name, same as open_archive's sibling sort).
        for key, parts in orders.items():
            _, members = scan.sets[key]
            scan.sets[key] = (min(parts)[2], members)
        return scan

    def _on_done_callback(
        self, key: _SetKey, fingerprint: _Fingerprint
    ) -> Callable[[str | None], None]:
        """The submission's completion callback (runs on the import worker)."""

        def on_done(error: str | None) -> None:
            with self._lock:
                state = self._states.get(key)
                if state is None:
                    return  # set vanished while importing
                state.inflight = False
                if state.fingerprint != fingerprint:
                    # Changed mid-import (a late part landed): the normal
                    # debounce re-imports it once quiet; dedup absorbs overlap.
                    return
                if error is None:
                    state.imported = True
                    state.failures = 0
                    state.skip_remaining = 0
                    # The producing set recovered: a healthy system must not
                    # keep showing its stale failure (review I2).
                    self._clear_error_for(key)
                else:
                    state.failures += 1
                    state.skip_remaining = min(2 ** (state.failures - 1), _MAX_BACKOFF_CYCLES)
                    self._last_error = f"{state.representative}: {error}"
                    self._last_error_key = key
                    _logger.warning(
                        "watch import failed (%s), retrying in %d cycle(s): %s",
                        state.representative,
                        state.skip_remaining,
                        error,
                    )

        return on_done

    # -- status --------------------------------------------------------------

    def snapshot(self) -> WatchRuntime:
        """Runtime snapshot for the status service (DTO copies only)."""
        with self._lock:
            pending: list[WatchPendingSet] = []
            for state in sorted(self._states.values(), key=lambda s: str(s.representative)):
                if state.inflight or state.imported:
                    continue  # running imports show on the imports page
                if state.skip_remaining > 0:
                    pending.append(
                        WatchPendingSet(
                            path=str(state.representative),
                            state="backoff",
                            retry_in_cycles=state.skip_remaining,
                        )
                    )
                else:
                    pending.append(
                        WatchPendingSet(path=str(state.representative), state="stabilizing")
                    )
            return WatchRuntime(
                last_scan_at=self._last_scan_at,
                pending=pending,
                last_error=self._last_error,
            )

    # -- thread wrapper (thin; run_cycle carries the logic) -------------------

    def start(self) -> None:
        """Spawn the daemon polling thread (serve lifespan only; idempotent)."""
        with self._lock:
            if not self._configured:
                raise RuntimeError("FolderWatcher.start() before configure()")
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="potluck-watch", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                self.run_cycle()
            except Exception as exc:  # the poller must never die mid-serve
                _logger.exception("watch cycle failed")
                with self._lock:
                    self._last_error = str(exc) or type(exc).__name__
                    self._last_error_key = None  # not attributable to one set
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


def _submit_unconfigured(path: Path, on_done: Callable[[str | None], None]) -> None:
    raise RuntimeError("FolderWatcher used before configure()")
