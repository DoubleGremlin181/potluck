"""FolderWatcher state machine (#151): debounce, backoff, multi-part grouping.

Every test drives ``run_cycle()`` synchronously over real tmp folders — no
watcher thread, no sleeps, fully deterministic under ``-n auto``. File-change
events are literal writes between cycles (each write changes size and/or
mtime_ns, which is exactly what the fingerprint watches). The thread wrapper
gets one lifecycle test at the bottom.

Reaction-time arithmetic (the #98 "watch-folder react < 30 s" budget,
asserted structurally here instead of sleeping): a set is submitted on the
SECOND consecutive scan observing an unchanged fingerprint. A drop that
finishes between scans is therefore first seen on the next scan (≤ 1
interval) and submitted one scan later (≤ 1 more interval) — submission
happens within 2 cycles of the drop, and within 1 cycle (< 30 s at the
default 30 s interval) of the debounce confirming stability. The shortened
intervals used by the integration tier make the observed wall-clock react
well under the 30 s acceptance bound.
"""

import threading
import time
from collections.abc import Callable
from pathlib import Path

from potluck.core.errors import ImportInProgressError
from potluck.services.watch_manager import FolderWatcher


class SubmitRecorder:
    """Fake submit seam: records claims, lets tests settle them later."""

    def __init__(self, *, busy: bool = False) -> None:
        self.busy = busy
        self.calls: list[Path] = []
        self._pending: list[Callable[[str | None], None]] = []

    def __call__(self, path: Path, on_done: Callable[[str | None], None]) -> None:
        if self.busy:
            raise ImportInProgressError("an import is already running")
        self.calls.append(path)
        self._pending.append(on_done)

    def settle(self, error: str | None = None) -> None:
        """Complete the oldest outstanding import (None = success)."""
        self._pending.pop(0)(error)


def make_watcher(
    folders: list[Path],
    submit: SubmitRecorder,
    *,
    enabled: Callable[[], bool] | None = None,
    interval_s: float = 0.01,
) -> FolderWatcher:
    watcher = FolderWatcher()
    watcher.configure(
        folders=tuple(folders),
        interval_s=interval_s,
        enabled=enabled if enabled is not None else lambda: True,
        submit=submit,
    )
    return watcher


def pending_states(watcher: FolderWatcher) -> dict[str, str]:
    return {p.path: p.state for p in watcher.snapshot().pending}


# ---------------------------------------------------------------------------
# Debounce: two-scan set-quiet rule
# ---------------------------------------------------------------------------


def test_stable_twice_submits_exactly_once(tmp_path: Path) -> None:
    folder = tmp_path / "watched"
    folder.mkdir()
    submit = SubmitRecorder()
    watcher = make_watcher([folder], submit)

    archive = folder / "export.zip"
    archive.write_bytes(b"PK-fake")

    watcher.run_cycle()  # first sight: stabilizing, no submit yet
    assert submit.calls == []
    assert pending_states(watcher) == {str(archive): "stabilizing"}

    watcher.run_cycle()  # unchanged across two consecutive scans -> submit
    assert submit.calls == [archive]

    submit.settle(None)  # import completed
    watcher.run_cycle()  # already imported: never resubmitted
    watcher.run_cycle()
    assert submit.calls == [archive]
    assert watcher.snapshot().pending == []


def test_growing_file_never_submits(tmp_path: Path) -> None:
    """A file mid-copy (fingerprint changing every scan) is never claimed."""
    folder = tmp_path / "watched"
    folder.mkdir()
    submit = SubmitRecorder()
    watcher = make_watcher([folder], submit)

    archive = folder / "big.zip"
    for i in range(1, 6):
        archive.write_bytes(b"x" * (1000 * i))
        watcher.run_cycle()
        assert submit.calls == []
        assert pending_states(watcher) == {str(archive): "stabilizing"}

    # The copy finishes: one confirming scan later the set is claimed.
    watcher.run_cycle()
    assert submit.calls == [archive]


def test_non_archive_files_ignored(tmp_path: Path) -> None:
    folder = tmp_path / "watched"
    folder.mkdir()
    (folder / "notes.txt").write_bytes(b"not an archive")
    (folder / "photo.jpg").write_bytes(b"jpeg")
    (folder / "archive.zip.part").write_bytes(b"mid-download")
    (folder / "subdir").mkdir()
    submit = SubmitRecorder()
    watcher = make_watcher([folder], submit)

    watcher.run_cycle()
    watcher.run_cycle()
    assert submit.calls == []
    assert watcher.snapshot().pending == []


def test_tgz_and_tar_gz_are_candidates(tmp_path: Path) -> None:
    folder = tmp_path / "watched"
    folder.mkdir()
    (folder / "a.tgz").write_bytes(b"gz1")
    (folder / "b.tar.gz").write_bytes(b"gz2")
    submit = SubmitRecorder()
    watcher = make_watcher([folder], submit)

    watcher.run_cycle()
    watcher.run_cycle()
    # The fake submit never claims busy, so both sets are claimed this cycle.
    assert sorted(submit.calls) == [folder / "a.tgz", folder / "b.tar.gz"]


def test_deleted_file_state_dropped(tmp_path: Path) -> None:
    folder = tmp_path / "watched"
    folder.mkdir()
    archive = folder / "gone.zip"
    archive.write_bytes(b"data")
    submit = SubmitRecorder()
    watcher = make_watcher([folder], submit)

    watcher.run_cycle()
    archive.unlink()
    watcher.run_cycle()
    assert submit.calls == []
    assert watcher.snapshot().pending == []


# ---------------------------------------------------------------------------
# Disabled / missing folders
# ---------------------------------------------------------------------------


def test_disabled_skips_scan_entirely(tmp_path: Path) -> None:
    folder = tmp_path / "watched"
    folder.mkdir()
    (folder / "export.zip").write_bytes(b"data")
    enabled = {"value": False}
    submit = SubmitRecorder()
    watcher = make_watcher([folder], submit, enabled=lambda: enabled["value"])

    watcher.run_cycle()
    watcher.run_cycle()
    assert submit.calls == []
    assert watcher.snapshot().last_scan_at is None  # disabled cycles never scan

    # Re-enabling picks the file up with the normal two-scan debounce.
    enabled["value"] = True
    watcher.run_cycle()
    assert submit.calls == []
    watcher.run_cycle()
    assert len(submit.calls) == 1
    assert watcher.snapshot().last_scan_at is not None


def test_missing_folder_tolerated_never_fatal(tmp_path: Path) -> None:
    present = tmp_path / "present"
    present.mkdir()
    (present / "ok.zip").write_bytes(b"data")
    missing = tmp_path / "missing"
    submit = SubmitRecorder()
    watcher = make_watcher([missing, present], submit)

    watcher.run_cycle()  # missing folder: warned, skipped, cycle continues
    watcher.run_cycle()
    assert submit.calls == [present / "ok.zip"]


# ---------------------------------------------------------------------------
# Multi-part sets: quiet as a WHOLE before one representative claim
# ---------------------------------------------------------------------------


def test_multipart_set_submits_one_representative(tmp_path: Path) -> None:
    folder = tmp_path / "watched"
    folder.mkdir()
    part1 = folder / "takeout-20240101T000000Z-001.zip"
    part2 = folder / "takeout-20240101T000000Z-002.zip"
    part1.write_bytes(b"part one")
    part2.write_bytes(b"part two")
    submit = SubmitRecorder()
    watcher = make_watcher([folder], submit)

    watcher.run_cycle()
    watcher.run_cycle()
    # ONE claim for the whole set, via its first part (open_archive
    # auto-groups the siblings).
    assert submit.calls == [part1]


def test_multipart_part_arriving_between_scans_resets_debounce(tmp_path: Path) -> None:
    """A new part changes the SET fingerprint: the whole set must go quiet
    again before it is claimed — the mid-arrival guard."""
    folder = tmp_path / "watched"
    folder.mkdir()
    part1 = folder / "takeout-20240101T000000Z-001.zip"
    part1.write_bytes(b"part one")
    submit = SubmitRecorder()
    watcher = make_watcher([folder], submit)

    watcher.run_cycle()  # set = {part1}, stabilizing
    part2 = folder / "takeout-20240101T000000Z-002.zip"
    part2.write_bytes(b"part two")
    watcher.run_cycle()  # set fingerprint changed (new member): NOT submitted
    assert submit.calls == []

    watcher.run_cycle()  # {part1, part2} unchanged twice -> claim part1
    assert submit.calls == [part1]


def test_unrelated_sets_not_grouped(tmp_path: Path) -> None:
    """Different stems (and plain singles) are independent sets."""
    folder = tmp_path / "watched"
    folder.mkdir()
    (folder / "takeout-20240101T000000Z-001.zip").write_bytes(b"a")
    (folder / "takeout-20990101T000000Z-001.zip").write_bytes(b"b")
    (folder / "ynab.zip").write_bytes(b"c")
    submit = SubmitRecorder()
    watcher = make_watcher([folder], submit)

    watcher.run_cycle()
    assert len(pending_states(watcher)) == 3


# ---------------------------------------------------------------------------
# Claim-busy: retry next cycle, zero state change
# ---------------------------------------------------------------------------


def test_claim_busy_leaves_state_untouched_and_retries(tmp_path: Path) -> None:
    folder = tmp_path / "watched"
    folder.mkdir()
    archive = folder / "export.zip"
    archive.write_bytes(b"data")
    submit = SubmitRecorder(busy=True)
    watcher = make_watcher([folder], submit)

    watcher.run_cycle()
    watcher.run_cycle()  # eligible, but the manager is busy
    watcher.run_cycle()
    assert submit.calls == []
    # Still pending (not stuck inflight/backoff) …
    assert pending_states(watcher) == {str(archive): "stabilizing"}

    # … and the moment the manager frees up, the very next cycle claims it.
    submit.busy = False
    watcher.run_cycle()
    assert submit.calls == [archive]


# ---------------------------------------------------------------------------
# Backoff: failures skip 1, 2, 4, … up to 32 cycles; re-drop resets
# ---------------------------------------------------------------------------


def _cycles_until_next_submit(watcher: FolderWatcher, submit: SubmitRecorder, cap: int) -> int:
    """Run cycles until submit fires again; return how many SKIPPED cycles preceded it."""
    before = len(submit.calls)
    for skipped in range(cap + 2):
        watcher.run_cycle()
        if len(submit.calls) > before:
            return skipped
    raise AssertionError(f"no resubmission within {cap + 2} cycles")


def test_backoff_progression_1_2_4_capped(tmp_path: Path) -> None:
    folder = tmp_path / "watched"
    folder.mkdir()
    archive = folder / "corrupt.zip"
    archive.write_bytes(b"not a zip")
    submit = SubmitRecorder()
    watcher = make_watcher([folder], submit)

    watcher.run_cycle()
    watcher.run_cycle()
    assert len(submit.calls) == 1

    # Failure n leads to skipping min(2**(n-1), 32) cycles before the retry.
    for expected_skip in [1, 2, 4, 8, 16, 32, 32]:
        submit.settle("corrupt or unreadable archive")
        assert pending_states(watcher) == {str(archive): "backoff"}
        assert _cycles_until_next_submit(watcher, submit, expected_skip) == expected_skip

    # The error is surfaced on the runtime status.
    submit.settle("corrupt or unreadable archive")
    assert watcher.snapshot().last_error is not None
    assert "corrupt" in str(watcher.snapshot().last_error)


def test_backoff_retry_in_cycles_reported(tmp_path: Path) -> None:
    folder = tmp_path / "watched"
    folder.mkdir()
    (folder / "corrupt.zip").write_bytes(b"junk")
    submit = SubmitRecorder()
    watcher = make_watcher([folder], submit)

    watcher.run_cycle()
    watcher.run_cycle()
    submit.settle("boom")
    [pending] = watcher.snapshot().pending
    assert pending.state == "backoff"
    assert pending.retry_in_cycles == 1

    # Second failure: skip 2 cycles, reported as they count down.
    watcher.run_cycle()  # skips (retry_in_cycles 1 -> 0)
    watcher.run_cycle()  # resubmits
    submit.settle("boom again")
    [pending] = watcher.snapshot().pending
    assert pending.retry_in_cycles == 2


def test_fingerprint_change_resets_backoff(tmp_path: Path) -> None:
    """A re-dropped (fixed) file gets a FRESH attempt immediately: the
    fingerprint change wipes failures and backoff."""
    folder = tmp_path / "watched"
    folder.mkdir()
    archive = folder / "export.zip"
    archive.write_bytes(b"corrupt bytes")
    submit = SubmitRecorder()
    watcher = make_watcher([folder], submit)

    watcher.run_cycle()
    watcher.run_cycle()
    for _ in range(4):  # pile up failures -> deep backoff
        submit.settle("corrupt")
        _cycles_until_next_submit(watcher, submit, 32)
    submit.settle("corrupt")
    assert pending_states(watcher) == {str(archive): "backoff"}

    archive.write_bytes(b"fixed content, different bytes")  # the re-drop
    watcher.run_cycle()  # change detected: backoff gone, stabilizing again
    assert pending_states(watcher) == {str(archive): "stabilizing"}
    before = len(submit.calls)
    watcher.run_cycle()  # stable twice -> immediate fresh claim
    assert len(submit.calls) == before + 1


def test_success_after_change_reimports(tmp_path: Path) -> None:
    """An imported set whose fingerprint changes (bigger re-export dropped
    under the same name) is re-imported after going quiet again."""
    folder = tmp_path / "watched"
    folder.mkdir()
    archive = folder / "export.zip"
    archive.write_bytes(b"v1")
    submit = SubmitRecorder()
    watcher = make_watcher([folder], submit)

    watcher.run_cycle()
    watcher.run_cycle()
    submit.settle(None)
    assert len(submit.calls) == 1

    archive.write_bytes(b"v2 - new export bytes")
    watcher.run_cycle()
    watcher.run_cycle()
    assert submit.calls == [archive, archive]


def test_inflight_set_not_reclaimed(tmp_path: Path) -> None:
    """While an import is running the set is neither pending nor resubmitted."""
    folder = tmp_path / "watched"
    folder.mkdir()
    archive = folder / "export.zip"
    archive.write_bytes(b"data")
    submit = SubmitRecorder()
    watcher = make_watcher([folder], submit)

    watcher.run_cycle()
    watcher.run_cycle()
    assert len(submit.calls) == 1
    watcher.run_cycle()  # import still running (never settled)
    watcher.run_cycle()
    assert len(submit.calls) == 1
    assert watcher.snapshot().pending == []


# ---------------------------------------------------------------------------
# Thread wrapper: one lifecycle test (start/stop/join, no leaked thread)
# ---------------------------------------------------------------------------


def test_thread_lifecycle_start_stop_join(tmp_path: Path) -> None:
    folder = tmp_path / "watched"
    folder.mkdir()
    (folder / "export.zip").write_bytes(b"data")
    submit = SubmitRecorder()
    watcher = make_watcher([folder], submit, interval_s=0.005)

    watcher.start()
    # The loop runs a first cycle immediately and keeps polling: the two-scan
    # debounce completes without any external nudge. Deadline-bounded poll,
    # never a blind sleep (import-manager test convention).
    deadline = time.monotonic() + 30.0
    while not submit.calls and time.monotonic() < deadline:
        time.sleep(0.005)
    assert submit.calls, "watcher thread never claimed the stable set"

    watcher.stop()
    watcher.join(5.0)
    alive = [t.name for t in threading.enumerate() if t.name == "potluck-watch" and t.is_alive()]
    assert alive == [], f"leaked watcher thread: {alive}"
