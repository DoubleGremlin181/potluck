"""gdrive_pulls table: Drive Takeout auto-pull tracking (#152).

All gdrive_pulls SQL is owned here; nothing outside storage/ builds it.
Batch-first: one IN(...) query per candidate batch, one executemany per
recorded set — never per-row round-trips.
"""

import sqlite3
from datetime import datetime

from potluck.models.gdrive import GDrivePullRecord
from potluck.storage.items import dt_to_iso, iso_to_dt


def filter_pulled(conn: sqlite3.Connection, file_ids: list[str]) -> set[str]:
    """The subset of *file_ids* already recorded as pulled (one IN query)."""
    if not file_ids:
        return set()
    placeholders = ",".join("?" * len(file_ids))
    rows = conn.execute(
        f"SELECT file_id FROM gdrive_pulls WHERE file_id IN ({placeholders})",  # noqa: S608
        file_ids,
    ).fetchall()
    return {str(row[0]) for row in rows}


def record_pulls(conn: sqlite3.Connection, records: list[GDrivePullRecord]) -> None:
    """Record a downloaded set (one executemany). OR REPLACE: a crash-recovery
    re-pull of a partially recorded set must be idempotent."""
    conn.executemany(
        """INSERT OR REPLACE INTO gdrive_pulls
               (file_id, name, md5, set_stem, local_path, pulled_at, pruned_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                r.file_id,
                r.name,
                r.md5,
                r.set_stem,
                r.local_path,
                dt_to_iso(r.pulled_at),
                dt_to_iso(r.pruned_at) if r.pruned_at is not None else None,
            )
            for r in records
        ],
    )


def count_pulls(conn: sqlite3.Connection) -> int:
    """Total recorded pulls (status surface)."""
    return int(conn.execute("SELECT count(*) FROM gdrive_pulls").fetchone()[0])


def list_prunable(conn: sqlite3.Connection) -> list[GDrivePullRecord]:
    """Un-pruned rows whose SET verifiably imported (decision doc §6).

    Two conditions must hold for a set (review I1 — the gate for a
    destructive files.delete must be sound on its own):

    - some row of the set matches a COMPLETED imports run by local path
      ('completed' is the schema's CHECK value; failed/running never
      qualify), AND
    - that run STARTED strictly after the set's newest pulled_at. Rows are
      recorded only after every part is renamed into place, so such a run
      necessarily opened the fully published set — and opening any part
      loads all on-disk siblings, so it imported the whole set. A run
      predating the pulls may be a stale import of a partial publish
      (crash between renames) and never qualifies; equality is excluded
      too — when in doubt, don't prune.
    """
    rows = conn.execute(
        """SELECT p.file_id, p.name, p.md5, p.set_stem, p.local_path,
                  p.pulled_at, p.pruned_at
           FROM gdrive_pulls p
           WHERE p.pruned_at IS NULL
             AND EXISTS (
                 SELECT 1
                 FROM gdrive_pulls p2
                 JOIN imports i ON i.path = p2.local_path
                 WHERE p2.set_stem = p.set_stem
                   AND i.status = 'completed'
                   -- Freshness: both columns are dt_to_iso strings (same
                   -- format), so lexical comparison is chronological.
                   AND i.started_at > (SELECT max(p3.pulled_at)
                                       FROM gdrive_pulls p3
                                       WHERE p3.set_stem = p.set_stem)
             )
           ORDER BY p.name"""
    ).fetchall()
    return [
        GDrivePullRecord(
            file_id=str(row[0]),
            name=str(row[1]),
            md5=None if row[2] is None else str(row[2]),
            set_stem=str(row[3]),
            local_path=str(row[4]),
            pulled_at=iso_to_dt(str(row[5])),
            pruned_at=None if row[6] is None else iso_to_dt(str(row[6])),
        )
        for row in rows
    ]


def mark_pruned(conn: sqlite3.Connection, file_ids: list[str], when: datetime) -> None:
    """Stamp pruned_at on *file_ids* (one executemany)."""
    conn.executemany(
        "UPDATE gdrive_pulls SET pruned_at = ? WHERE file_id = ?",
        [(dt_to_iso(when), fid) for fid in file_ids],
    )
