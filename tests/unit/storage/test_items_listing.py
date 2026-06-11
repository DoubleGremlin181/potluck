"""Storage-level tests for list_item_rows: query plan, COUNT shape, snapshot."""

import sqlite3
from typing import Any

from potluck.models.items import ItemSort
from potluck.services.context import AppContext
from potluck.storage.items import list_item_rows
from tests.conftest import insert_import, insert_source


def _seed(ctx: AppContext, count: int = 5) -> None:
    def _setup(conn: sqlite3.Connection) -> None:
        src = insert_source(conn, "listing-src")
        imp = insert_import(conn, src)
        for i in range(count):
            conn.execute(
                """INSERT INTO items (source_id, import_id, kind, content_hash, ts, title)
                   VALUES (?, ?, 'note', ?, ?, ?)""",
                (src, imp, f"hash-{i}", f"2024-01-{i + 1:02d}T00:00:00+00:00", f"t{i}"),
            )

    ctx.db.write(_setup)


class _RecordingConn:
    """Duck-typed connection wrapper: records executed SQL, optional post-hook."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.statements: list[tuple[str, list[object]]] = []
        self.after_count_execute: Any = None

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        cursor = self._conn.execute(sql, params)
        self.statements.append((sql, list(params)))
        if self.after_count_execute is not None and "COUNT(*)" in sql:
            hook = self.after_count_execute
            self.after_count_execute = None
            hook()
        return cursor

    @property
    def in_transaction(self) -> bool:
        return self._conn.in_transaction


def _list_defaults(conn: Any, *, sort: ItemSort = ItemSort.TS_DESC, limit: int = 20) -> Any:
    return list_item_rows(
        conn,
        kinds=None,
        sources=None,
        since_iso=None,
        until_iso=None,
        sort=sort,
        limit=limit,
        offset=0,
    )


def test_default_listing_uses_ts_index_no_temp_btree(ctx: AppContext) -> None:
    """The unfiltered ts_desc page query must early-exit on an index, not
    scan-and-sort: EXPLAIN shows idx_items_ts and no temp B-tree."""
    _seed(ctx)
    with ctx.db.read() as conn:
        recording = _RecordingConn(conn)
        _list_defaults(recording)
        page_sql, page_params = next(
            (sql, params) for sql, params in recording.statements if "ORDER BY" in sql
        )
        plan = "\n".join(
            str(row[3])
            for row in conn.execute(f"EXPLAIN QUERY PLAN {page_sql}", page_params).fetchall()
        )

    assert "idx_items_ts" in plan, plan
    assert "TEMP B-TREE" not in plan, plan


def test_unfiltered_count_does_not_join_sources(ctx: AppContext) -> None:
    """The NOT NULL FK guarantees the sources join can never change COUNT(*);
    the unfiltered count must not pay for it."""
    _seed(ctx)
    with ctx.db.read() as conn:
        recording = _RecordingConn(conn)
        rows, total = _list_defaults(recording)
        count_sql = next(sql for sql, _ in recording.statements if "COUNT(*)" in sql)

    assert total == 5
    assert len(rows) == 5
    assert "JOIN" not in count_sql, count_sql


def test_source_filtered_count_still_correct(ctx: AppContext) -> None:
    _seed(ctx)
    with ctx.db.read() as conn:
        rows, total = list_item_rows(
            conn,
            kinds=None,
            sources=["listing-src"],
            since_iso=None,
            until_iso=None,
            sort=ItemSort.TS_DESC,
            limit=20,
            offset=0,
        )
        _, none_total = list_item_rows(
            conn,
            kinds=None,
            sources=["no-such-src"],
            since_iso=None,
            until_iso=None,
            sort=ItemSort.TS_DESC,
            limit=20,
            offset=0,
        )

    assert (len(rows), total) == (5, 5)
    assert none_total == 0


def test_count_and_page_share_one_snapshot(ctx: AppContext) -> None:
    """A write committed between the COUNT and the page SELECT must not skew
    the pair: both run against one read snapshot, so total == len(rows)."""
    _seed(ctx)

    def _sneaky_insert() -> None:
        def _w(conn: sqlite3.Connection) -> None:
            conn.execute(
                """INSERT INTO items (source_id, import_id, kind, content_hash, ts, title)
                   SELECT source_id, import_id, 'note', 'hash-sneaky', ts, 'sneaky'
                   FROM items LIMIT 1"""
            )

        ctx.db.write(_w)

    with ctx.db.read() as conn:
        recording = _RecordingConn(conn)
        recording.after_count_execute = _sneaky_insert
        rows, total = _list_defaults(recording, limit=100)

    assert total == len(rows) == 5
    assert not conn.in_transaction
