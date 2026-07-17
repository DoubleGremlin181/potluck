"""DrivePuller state machine (#152): pull, set-atomic publish, backoff,
offline quiet-skip, reauth latch, prune gating.

Every test drives ``run_cycle()`` synchronously against a MockDrive transport
and fake ops — no thread, no sleeps, no network, deterministic under
``-n auto``. The thread wrapper gets one lifecycle test at the bottom
(mirroring the #151 watcher's test layout).
"""

import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from potluck.ingest.gdrive import (
    DRIVE_SCOPE_FULL,
    DRIVE_SCOPE_READONLY,
    DriveClient,
)
from potluck.models.gdrive import GDrivePullRecord, StoredToken
from potluck.services.gdrive_manager import _MAX_BACKOFF_CYCLES, DrivePuller, PullerOps
from tests.conftest import MockDrive

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


class OpsHarness:
    """Fake service wiring: in-memory pull ledger + scriptable knobs."""

    def __init__(
        self,
        mock: MockDrive,
        token_path: Path,
        *,
        scopes: list[str] | None = None,
        prune: bool = False,
    ) -> None:
        self.mock = mock
        self.token_path = token_path
        self.enabled_value = True
        self.prune_value = prune
        self.token: StoredToken | None = StoredToken(
            refresh_token="rtok-1",
            client_id="cid-1",
            scopes=scopes if scopes is not None else [DRIVE_SCOPE_READONLY],
            obtained_at=_T0,
        )
        self.fingerprint: object = 1
        self.recorded: list[GDrivePullRecord] = []
        self.prunable: list[GDrivePullRecord] = []
        self.pruned: list[str] = []

    def _make_client(self) -> DriveClient | None:
        if self.token is None:
            return None
        return DriveClient(
            client_id="cid-1",
            client_secret="csecret-1",
            token=self.token,
            token_path=self.token_path,
            transport=self.mock.transport(),
        )

    def ops(self) -> PullerOps:
        return PullerOps(
            enabled=lambda: self.enabled_value,
            make_client=self._make_client,
            token_fingerprint=lambda: self.fingerprint,
            filter_pulled=lambda ids: {i for i in ids if i in {r.file_id for r in self.recorded}},
            record_pulls=self.recorded.extend,
            prune_enabled=lambda: self.prune_value,
            list_prunable=lambda: list(self.prunable),
            mark_pruned=self.pruned.extend,
        )


def make_puller(
    harness: OpsHarness,
    downloads: Path,
    *,
    folder_name: str = "Takeout",
    interval_s: float = 0.01,
) -> DrivePuller:
    puller = DrivePuller()
    puller.configure(
        ops=harness.ops(),
        downloads_dir=downloads,
        folder_name=folder_name,
        interval_s=interval_s,
    )
    return puller


def seed_takeout_set(mock: MockDrive, stem: str = "takeout-20260101T000000Z") -> list[str]:
    """A 2-part zip set inside a Takeout folder; returns the file ids."""
    folder = mock.add_folder("Takeout")
    return [
        mock.add_file(folder, f"{stem}-001.zip", b"part-one-bytes"),
        mock.add_file(folder, f"{stem}-002.zip", b"part-two-bytes!!"),
    ]


# ---------------------------------------------------------------------------
# Happy path: pull, publish, record, never re-pull
# ---------------------------------------------------------------------------


def test_pull_downloads_set_publishes_and_records(tmp_path: Path) -> None:
    mock = MockDrive()
    ids = seed_takeout_set(mock)
    harness = OpsHarness(mock, tmp_path / "tok.json")
    downloads = tmp_path / "downloads"
    puller = make_puller(harness, downloads)

    puller.run_cycle()

    names = sorted(p.name for p in downloads.iterdir())
    assert names == [
        "takeout-20260101T000000Z-001.zip",
        "takeout-20260101T000000Z-002.zip",
    ]  # final names only — no .part droppings
    assert (downloads / names[0]).read_bytes() == b"part-one-bytes"
    assert sorted(r.file_id for r in harness.recorded) == sorted(ids)
    assert all(r.set_stem == "takeout-20260101T000000Z" for r in harness.recorded)
    assert all(r.md5 is not None for r in harness.recorded)
    snapshot = puller.snapshot()
    assert snapshot.last_pull_at is not None
    assert snapshot.last_check_at is not None
    assert snapshot.last_error is None

    # Next cycle: ids are recorded — nothing re-downloaded.
    downloads_before = len(mock.download_ranges)
    puller.run_cycle()
    assert len(mock.download_ranges) == downloads_before
    assert puller.snapshot().last_pull_at == snapshot.last_pull_at


def test_sets_pull_oldest_export_first(tmp_path: Path) -> None:
    """Timestamped stems sort chronologically: the older export lands (and is
    recorded) before the newer one."""
    mock = MockDrive()
    folder = mock.add_folder("Takeout")
    newer = mock.add_file(folder, "takeout-20260301T000000Z-001.zip", b"march")
    older = mock.add_file(folder, "takeout-20260101T000000Z-001.zip", b"january")
    harness = OpsHarness(mock, tmp_path / "tok.json")
    puller = make_puller(harness, tmp_path / "downloads")

    puller.run_cycle()
    assert [r.file_id for r in harness.recorded] == [older, newer]


def test_non_archive_children_ignored(tmp_path: Path) -> None:
    mock = MockDrive()
    folder = mock.add_folder("Takeout")
    mock.add_file(folder, "notes.txt", b"not an archive")
    harness = OpsHarness(mock, tmp_path / "tok.json")
    downloads = tmp_path / "downloads"
    puller = make_puller(harness, downloads)

    puller.run_cycle()
    assert harness.recorded == []
    assert list(downloads.iterdir()) == []


# ---------------------------------------------------------------------------
# Disabled / unauthorized: quiet no-ops
# ---------------------------------------------------------------------------


def test_disabled_cycle_does_nothing(tmp_path: Path) -> None:
    mock = MockDrive()
    seed_takeout_set(mock)
    harness = OpsHarness(mock, tmp_path / "tok.json")
    harness.enabled_value = False
    puller = make_puller(harness, tmp_path / "downloads")

    puller.run_cycle()
    assert harness.recorded == []
    assert puller.snapshot().last_check_at is None  # disabled cycles never check


def test_no_token_is_a_quiet_noop(tmp_path: Path) -> None:
    """Configured client but `potluck gdrive auth` never ran: the cycle is a
    no-op (status shows 'unauthorized' via the service; not a thread error)."""
    mock = MockDrive()
    harness = OpsHarness(mock, tmp_path / "tok.json")
    harness.token = None
    puller = make_puller(harness, tmp_path / "downloads")

    puller.run_cycle()
    snapshot = puller.snapshot()
    assert snapshot.last_check_at is not None
    assert snapshot.last_error is None
    assert mock.refresh_calls == 0


# ---------------------------------------------------------------------------
# Set-atomic publish: a failed member keeps the WHOLE set invisible (.part)
# ---------------------------------------------------------------------------


def test_failed_member_keeps_whole_set_unpublished(tmp_path: Path) -> None:
    mock = MockDrive()
    ids = seed_takeout_set(mock)
    mock.fail_download_ids = {ids[1]}  # second part always 503s
    harness = OpsHarness(mock, tmp_path / "tok.json")
    downloads = tmp_path / "downloads"
    puller = make_puller(harness, downloads)

    puller.run_cycle()
    # Nothing published, nothing recorded — the completed part waits as .part
    # (invisible to the #151 watcher, whose suffix filter skips it).
    assert sorted(p.name for p in downloads.iterdir()) == ["takeout-20260101T000000Z-001.zip.part"]
    assert harness.recorded == []
    snapshot = puller.snapshot()
    assert snapshot.last_error is not None
    assert snapshot.backoff_cycles == 1  # first failure: sit out one cycle

    # Recovery: the fault clears; backoff expires; the set publishes whole.
    mock.fail_download_ids = set()
    puller.run_cycle()  # backoff sit-out
    assert harness.recorded == []
    puller.run_cycle()  # retry: part 1 preflights complete, part 2 downloads
    assert sorted(p.name for p in downloads.iterdir()) == [
        "takeout-20260101T000000Z-001.zip",
        "takeout-20260101T000000Z-002.zip",
    ]
    assert sorted(r.file_id for r in harness.recorded) == sorted(ids)
    assert puller.snapshot().last_error is None
    assert puller.snapshot().backoff_cycles is None


def test_backoff_progression_1_2_4_capped(tmp_path: Path) -> None:
    mock = MockDrive()
    ids = seed_takeout_set(mock)
    mock.fail_download_ids = set(ids)
    harness = OpsHarness(mock, tmp_path / "tok.json")
    puller = make_puller(harness, tmp_path / "downloads")

    # Failure n sits out min(2**(n-1), 4) cycles before the retry. Attempts
    # are counted via refresh_calls: each ACTIVE cycle builds a fresh client
    # whose first Drive call refreshes lazily, exactly once — while sit-out
    # cycles make no token/Drive traffic at all.
    attempts = 1
    puller.run_cycle()
    assert mock.refresh_calls == attempts
    for expected_skip in [1, 2, 4, 4]:
        for _ in range(expected_skip):
            puller.run_cycle()  # sitting out: zero Drive traffic
            assert mock.refresh_calls == attempts
        puller.run_cycle()  # the retry
        attempts += 1
        assert mock.refresh_calls == attempts


def test_consecutive_failures_never_grow_unbounded_state(tmp_path: Path) -> None:
    """The failure counter saturates once backoff hits its ceiling: years of
    consecutive daily failures must not keep inflating the exponent behind
    the min() clamp (task-12 review M5)."""
    mock = MockDrive()
    ids = seed_takeout_set(mock)
    mock.fail_download_ids = set(ids)
    harness = OpsHarness(mock, tmp_path / "tok.json")
    puller = make_puller(harness, tmp_path / "downloads")
    for _ in range(50):  # ~10 active failures — several past the backoff cap
        puller.run_cycle()
    assert 2 ** (puller._failures - 1) <= _MAX_BACKOFF_CYCLES  # saturated, not huge
    before = mock.refresh_calls
    while mock.refresh_calls == before:
        puller.run_cycle()  # drain any sit-out (≤ 4); the next active cycle fails
    assert puller.snapshot().backoff_cycles == _MAX_BACKOFF_CYCLES  # still capped


# ---------------------------------------------------------------------------
# Offline: a status fact, never an error (decision doc §8)
# ---------------------------------------------------------------------------


def test_offline_is_quiet_status_not_error(tmp_path: Path) -> None:
    mock = MockDrive()
    seed_takeout_set(mock)
    mock.offline = True
    harness = OpsHarness(mock, tmp_path / "tok.json")
    puller = make_puller(harness, tmp_path / "downloads")

    puller.run_cycle()
    snapshot = puller.snapshot()
    assert snapshot.offline is True
    assert snapshot.last_error is None  # offline is normal for a laptop
    assert snapshot.backoff_cycles is None  # and never backs off

    mock.offline = False
    puller.run_cycle()  # connectivity is back: pull proceeds immediately
    assert puller.snapshot().offline is False
    assert harness.recorded != []


# ---------------------------------------------------------------------------
# Reauth latch: a dead refresh token stops Drive calls until the token changes
# ---------------------------------------------------------------------------


def test_invalid_grant_latches_until_token_file_changes(tmp_path: Path) -> None:
    mock = MockDrive()
    seed_takeout_set(mock)
    mock.refresh_error = "invalid_grant"
    harness = OpsHarness(mock, tmp_path / "tok.json")
    puller = make_puller(harness, tmp_path / "downloads")

    puller.run_cycle()
    snapshot = puller.snapshot()
    assert snapshot.reauth_required is True
    assert snapshot.last_error is not None and "gdrive auth" in snapshot.last_error
    assert mock.refresh_calls == 1

    puller.run_cycle()  # same dead token: no Drive/token calls at all
    puller.run_cycle()
    assert mock.refresh_calls == 1

    # `potluck gdrive auth` wrote a fresh token (fingerprint changes): retry.
    mock.refresh_error = None
    harness.fingerprint = 2
    puller.run_cycle()
    assert puller.snapshot().reauth_required is False
    assert puller.snapshot().last_error is None
    assert harness.recorded != []


# ---------------------------------------------------------------------------
# Prune: default off; scope-gated; exact recorded ids only
# ---------------------------------------------------------------------------


def _prunable_record(file_id: str) -> GDrivePullRecord:
    return GDrivePullRecord(
        file_id=file_id,
        name="takeout-20260101T000000Z-001.zip",
        md5="m",
        set_stem="takeout-20260101T000000Z",
        local_path="/downloads/takeout-20260101T000000Z-001.zip",
        pulled_at=_T0,
    )


def test_prune_disabled_never_deletes(tmp_path: Path) -> None:
    mock = MockDrive()
    ids = seed_takeout_set(mock)
    harness = OpsHarness(mock, tmp_path / "tok.json")  # prune=False default
    harness.prunable = [_prunable_record(ids[0])]
    puller = make_puller(harness, tmp_path / "downloads")

    puller.run_cycle()
    assert mock.deleted == []
    assert harness.pruned == []


def test_prune_without_full_scope_surfaces_error_never_escalates(tmp_path: Path) -> None:
    mock = MockDrive()
    ids = seed_takeout_set(mock)
    harness = OpsHarness(mock, tmp_path / "tok.json", prune=True)  # readonly scopes
    harness.prunable = [_prunable_record(ids[0])]
    puller = make_puller(harness, tmp_path / "downloads")

    puller.run_cycle()
    assert mock.deleted == []
    snapshot = puller.snapshot()
    assert snapshot.last_error is not None and "--prune" in snapshot.last_error
    # The pull itself still worked — the scope gate is not a cycle failure.
    assert harness.recorded != []
    assert snapshot.backoff_cycles is None


def test_prune_deletes_exactly_the_recorded_ids(tmp_path: Path) -> None:
    mock = MockDrive()
    folder = mock.add_folder("Takeout")
    imported = mock.add_file(folder, "takeout-20250101T000000Z-001.zip", b"old export")
    untouched = mock.add_file(folder, "takeout-20260101T000000Z-001.zip", b"new export")
    harness = OpsHarness(
        mock,
        tmp_path / "tok.json",
        scopes=[DRIVE_SCOPE_READONLY, DRIVE_SCOPE_FULL],
        prune=True,
    )
    # Only the OLD export's set is verified-imported (list_prunable outcome).
    harness.recorded = [_prunable_record(imported)]
    harness.prunable = [_prunable_record(imported)]
    puller = make_puller(harness, tmp_path / "downloads")

    puller.run_cycle()
    assert mock.deleted == [imported]
    assert harness.pruned == [imported]
    assert untouched in mock.files  # everything else in the folder untouched
    assert puller.snapshot().last_error is None


# ---------------------------------------------------------------------------
# Thread wrapper: start/stop/join, no leaked thread (leak hygiene, -n auto)
# ---------------------------------------------------------------------------


def test_thread_lifecycle_start_stop_join(tmp_path: Path) -> None:
    mock = MockDrive()
    seed_takeout_set(mock)
    harness = OpsHarness(mock, tmp_path / "tok.json")
    puller = make_puller(harness, tmp_path / "downloads", interval_s=0.005)

    puller.start()
    deadline = time.monotonic() + 30.0
    while not harness.recorded and time.monotonic() < deadline:
        time.sleep(0.005)
    assert harness.recorded, "puller thread never pulled the seeded set"
    assert puller.is_running()

    puller.stop()
    puller.join(5.0)
    alive = [t.name for t in threading.enumerate() if t.name == "potluck-gdrive" and t.is_alive()]
    assert alive == [], f"leaked puller thread: {alive}"
