"""Built-in benchmark scenarios.

P0 ships one placeholder that exercises the whole measured path (generators ->
writer thread -> executemany -> read back); real ingest/search scenarios with
hard budgets arrive with their features (P1+).
"""

import sqlite3
from pathlib import Path

from potluck.bench.registry import Scenario
from potluck.storage.db import Database
from potluck.testing.generators import synthetic_notes

_NOTE_COUNT = 5000


def _meta_roundtrip(workdir: Path) -> None:
    db = Database.open(workdir / "bench.db")
    try:
        rows = [
            (f"note:{i}", f"{note['title']}\n{note['text']}")
            for i, note in enumerate(synthetic_notes(_NOTE_COUNT))
        ]

        def insert(conn: sqlite3.Connection) -> None:
            conn.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", rows)

        db.write(insert)
        count = db.write(lambda c: int(c.execute("SELECT count(*) FROM meta").fetchone()[0]))
        if count != _NOTE_COUNT:
            raise RuntimeError(f"scenario integrity check failed: {count} != {_NOTE_COUNT}")
    finally:
        db.close()


ALL_SCENARIOS = [
    Scenario(name="meta_roundtrip_5k", tier="smoke", item_count=_NOTE_COUNT, run=_meta_roundtrip),
]
