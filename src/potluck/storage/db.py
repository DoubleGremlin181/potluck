"""SQLite access: standard pragmas, one writer thread, per-thread readers.

Connections run in autocommit mode: a single statement (including one
``executemany``) is atomic on its own, and multi-statement units take explicit
``BEGIN``/``COMMIT`` (see ``migrate.apply_migrations`` for the pattern).
"""

import sqlite3
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from potluck.storage.migrate import apply_migrations


def connect(path: Path, *, query_only: bool = False) -> sqlite3.Connection:
    """Open a connection with Potluck's standard pragmas applied."""
    conn = sqlite3.connect(path, autocommit=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    if query_only:
        conn.execute("PRAGMA query_only = ON")
    return conn


class Database:
    """Single-writer SQLite database.

    All mutations go through :meth:`write`, which runs the given closure on
    the one thread owning the sole write connection; WAL keeps readers live
    meanwhile. Reads go through :meth:`read`, yielding a query-only connection
    cached per calling thread (``check_same_thread`` is off, but each reader
    connection is only ever used by the thread that created it).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._writer_conn: sqlite3.Connection | None = None
        self._readers = threading.local()
        self._reader_conns: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="potluck-writer")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._writer.submit(self._init_writer).result()

    @classmethod
    def open(cls, path: Path) -> "Database":
        """Open (creating lazily if needed) and migrate the database at ``path``."""
        db = cls(path)
        db.write(apply_migrations)
        return db

    def _init_writer(self) -> None:
        self._writer_conn = connect(self._path)

    def write[T](self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run ``fn`` with the write connection on the writer thread; return its result."""
        return self._writer.submit(self._run_write, fn).result()

    def _run_write[T](self, fn: Callable[[sqlite3.Connection], T]) -> T:
        assert self._writer_conn is not None
        return fn(self._writer_conn)

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Yield this thread's query-only connection, creating it on first use."""
        conn: sqlite3.Connection | None = getattr(self._readers, "conn", None)
        if conn is None:
            conn = connect(self._path, query_only=True)
            self._readers.conn = conn
            with self._lock:
                self._reader_conns.append(conn)
        yield conn

    def close(self) -> None:
        """Close all connections and stop the writer thread."""

        def _close_writer() -> None:
            if self._writer_conn is not None:
                self._writer_conn.close()
                self._writer_conn = None

        self._writer.submit(_close_writer).result()
        self._writer.shutdown()
        with self._lock:
            for conn in self._reader_conns:
                conn.close()
            self._reader_conns.clear()
