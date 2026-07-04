"""Database: pragmas, writer thread, per-thread readers, concurrency."""

import sqlite3
import threading
from collections.abc import Iterator
from functools import partial
from pathlib import Path

import pytest

from potluck.core.paths import default_db_path
from potluck.storage.db import Database


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    """An open, migrated Database on a tmp file — the standard storage fixture."""
    database = Database.open(tmp_path / "test.db")
    yield database
    database.close()


def test_pragmas_applied_on_reader_and_writer(db: Database) -> None:
    with db.read() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert db.write(lambda c: c.execute("PRAGMA journal_mode").fetchone()[0]) == "wal"
    assert db.write(lambda c: c.execute("PRAGMA foreign_keys").fetchone()[0]) == 1


def test_read_connections_are_query_only(db: Database) -> None:
    with db.read() as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO meta (key, value) VALUES ('x', 'y')")


def test_writes_run_on_the_single_writer_thread(db: Database) -> None:
    names = {db.write(lambda c: threading.current_thread().name) for _ in range(5)}
    assert len(names) == 1
    assert names.pop().startswith("potluck-writer")


def test_db_file_created_lazily_at_default_path(isolated_dirs: Path) -> None:
    path = default_db_path()
    assert not path.exists()
    database = Database.open(path)
    try:
        assert path.exists()
        assert path.is_relative_to(isolated_dirs)
    finally:
        database.close()


def _insert_batch(i: int, conn: sqlite3.Connection) -> None:
    rows = [(f"k{i}-{j}", "v") for j in range(50)]
    conn.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", rows)


def test_concurrent_reads_while_writing(db: Database) -> None:
    """WAL + busy_timeout: readers never error while the writer churns."""
    errors: list[Exception] = []
    stop = threading.Event()

    def reader() -> None:
        try:
            while not stop.is_set():
                with db.read() as conn:
                    conn.execute("SELECT count(*) FROM meta").fetchone()
        except Exception as exc:  # noqa: BLE001 - collecting for assertion
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    try:
        for i in range(200):
            db.write(partial(_insert_batch, i))
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=10)
    assert errors == []
    count = db.write(lambda c: c.execute("SELECT count(*) FROM meta").fetchone()[0])
    assert count == 200 * 50


def test_write_async_runs_on_writer_thread(tmp_path: Path) -> None:
    """#199: write_async submits without blocking; the closure still runs on
    the single writer thread with the sole write connection."""
    import threading

    db = Database.open(tmp_path / "async.db")
    try:

        def probe(conn: sqlite3.Connection) -> str:
            conn.execute("INSERT INTO meta (key, value) VALUES ('k', 'v')")
            return threading.current_thread().name

        future = db.write_async(probe)
        thread_name = future.result()
        assert thread_name.startswith("potluck-writer")
        value = db.write(lambda c: c.execute("SELECT value FROM meta WHERE key='k'").fetchone()[0])
        assert value == "v"
    finally:
        db.close()


def test_bulk_import_mode_keeps_synchronous_normal(db: Database) -> None:
    """synchronous=OFF with checkpoints running risks corrupting the WHOLE
    database on power loss, not just losing recent commits — bulk mode must
    stay at NORMAL (#198 review 12). Pinned so OFF can't sneak back."""
    with db.bulk_import_mode():
        sync, cache, autockpt = db.write(
            lambda conn: (
                conn.execute("PRAGMA synchronous").fetchone()[0],
                conn.execute("PRAGMA cache_size").fetchone()[0],
                conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0],
            )
        )
    assert sync == 1  # NORMAL
    assert cache == -262144
    assert autockpt == 10000

    sync_after, cache_after, autockpt_after = db.write(
        lambda conn: (
            conn.execute("PRAGMA synchronous").fetchone()[0],
            conn.execute("PRAGMA cache_size").fetchone()[0],
            conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0],
        )
    )
    assert sync_after == 1
    assert cache_after == -2000
    assert autockpt_after == 1000
