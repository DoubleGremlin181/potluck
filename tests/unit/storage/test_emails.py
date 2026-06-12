"""storage/emails.py: satellite rows, parent reconciliation, thread queries."""

import json
import sqlite3

from potluck.models.drafts import EmailDraft
from potluck.services.context import AppContext
from potluck.storage.emails import (
    EmailRow,
    draft_to_email_row,
    insert_emails,
    list_thread_rows,
    resolve_email_parents,
)
from tests.conftest import insert_import, insert_item, insert_source


def _email_item(
    conn: sqlite3.Connection,
    source_id: int,
    import_id: int,
    *,
    n: int,
    message_id: str | None,
    in_reply_to: str | None = None,
    thread_key: str = "tk",
    from_addr: str | None = None,
    ts: str | None = None,
) -> int:
    item_id = insert_item(
        conn,
        source_id,
        import_id,
        content_hash=f"h{n}",
        kind="email",
        external_id=f"e{n}",
        ts=ts,
        title=f"subject {n}",
    )
    insert_emails(
        conn,
        [
            EmailRow(
                item_id=item_id,
                message_id=message_id,
                in_reply_to=in_reply_to,
                thread_key=thread_key,
                from_addr=from_addr,
                to_json="[]",
                cc_json="[]",
                labels_json="[]",
            )
        ],
    )
    return item_id


def test_draft_to_email_row_serializes_json() -> None:
    draft = EmailDraft(
        message_id="m@potluck.test",
        in_reply_to="p@potluck.test",
        thread_key="root@potluck.test",
        from_addr="alice@potluck.test",
        to_addrs=("bob@potluck.test", "carol@example.com"),
        cc_addrs=("dave@potluck.test",),
        labels=("Inbox", "Unread"),
    )
    row = draft_to_email_row(draft, item_id=7)
    assert row.item_id == 7
    assert row.message_id == "m@potluck.test"
    assert json.loads(row.to_json) == ["bob@potluck.test", "carol@example.com"]
    assert json.loads(row.cc_json) == ["dave@potluck.test"]
    assert json.loads(row.labels_json) == ["Inbox", "Unread"]


def test_insert_emails_replace_on_conflict(ctx: AppContext) -> None:
    def _go(conn: sqlite3.Connection) -> list[str]:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        item_id = _email_item(conn, sid, iid, n=1, message_id="m1")
        insert_emails(
            conn,
            [
                EmailRow(
                    item_id=item_id,
                    message_id="m1",
                    in_reply_to=None,
                    thread_key="tk",
                    from_addr=None,
                    to_json="[]",
                    cc_json="[]",
                    labels_json='["Archived"]',
                )
            ],
        )
        rows = conn.execute("SELECT labels_json FROM emails").fetchall()
        return [str(r[0]) for r in rows]

    labels = ctx.db.write(_go)
    assert labels == ['["Archived"]']


def test_resolve_parents_basic_chain(ctx: AppContext) -> None:
    def _go(conn: sqlite3.Connection) -> tuple[int, dict[int, int | None]]:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        root = _email_item(conn, sid, iid, n=1, message_id="m1")
        child = _email_item(conn, sid, iid, n=2, message_id="m2", in_reply_to="m1")
        grandchild = _email_item(conn, sid, iid, n=3, message_id="m3", in_reply_to="m2")
        resolved = resolve_email_parents(conn, sid)
        parents = {
            int(r[0]): r[1] for r in conn.execute("SELECT id, parent_id FROM items").fetchall()
        }
        assert parents[root] is None
        assert parents[child] == root
        assert parents[grandchild] == child
        return resolved, parents

    resolved, _ = ctx.db.write(_go)
    assert resolved == 2


def test_resolve_parents_forward_reference(ctx: AppContext) -> None:
    """A child stored BEFORE its parent still resolves (set-based, not ordered)."""

    def _go(conn: sqlite3.Connection) -> None:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        child = _email_item(conn, sid, iid, n=1, message_id="m1", in_reply_to="m2")
        parent = _email_item(conn, sid, iid, n=2, message_id="m2")
        resolve_email_parents(conn, sid)
        row = conn.execute("SELECT parent_id FROM items WHERE id = ?", (child,)).fetchone()
        assert row[0] == parent

    ctx.db.write(_go)


def test_resolve_parents_missing_parent_stays_null(ctx: AppContext) -> None:
    def _go(conn: sqlite3.Connection) -> None:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        orphan = _email_item(conn, sid, iid, n=1, message_id="m1", in_reply_to="gone")
        assert resolve_email_parents(conn, sid) == 0
        row = conn.execute("SELECT parent_id FROM items WHERE id = ?", (orphan,)).fetchone()
        assert row[0] is None

    ctx.db.write(_go)


def test_resolve_parents_duplicate_message_id_deterministic(ctx: AppContext) -> None:
    """Two candidate parents sharing a Message-ID: lowest item id wins."""

    def _go(conn: sqlite3.Connection) -> None:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        parent_a = _email_item(conn, sid, iid, n=1, message_id="dup")
        _email_item(conn, sid, iid, n=2, message_id="dup")
        child = _email_item(conn, sid, iid, n=3, message_id="m3", in_reply_to="dup")
        resolve_email_parents(conn, sid)
        row = conn.execute("SELECT parent_id FROM items WHERE id = ?", (child,)).fetchone()
        assert row[0] == parent_a

    ctx.db.write(_go)


def test_resolve_parents_self_reply_stays_null(ctx: AppContext) -> None:
    def _go(conn: sqlite3.Connection) -> None:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        item = _email_item(conn, sid, iid, n=1, message_id="m1", in_reply_to="m1")
        resolve_email_parents(conn, sid)
        row = conn.execute("SELECT parent_id FROM items WHERE id = ?", (item,)).fetchone()
        assert row[0] is None

    ctx.db.write(_go)


def test_resolve_parents_scoped_to_source(ctx: AppContext) -> None:
    """A parent in ANOTHER source never adopts this source's children."""

    def _go(conn: sqlite3.Connection) -> None:
        sid_a = insert_source(conn, "src-a")
        sid_b = insert_source(conn, "src-b")
        iid_a = insert_import(conn, sid_a)
        iid_b = insert_import(conn, sid_b)
        _email_item(conn, sid_b, iid_b, n=1, message_id="m1")
        child = _email_item(conn, sid_a, iid_a, n=2, message_id="m2", in_reply_to="m1")
        resolve_email_parents(conn, sid_a)
        row = conn.execute("SELECT parent_id FROM items WHERE id = ?", (child,)).fetchone()
        assert row[0] is None

    ctx.db.write(_go)


def test_list_thread_rows_ordered_by_ts(ctx: AppContext) -> None:
    def _go(conn: sqlite3.Connection) -> list[int]:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        late = _email_item(conn, sid, iid, n=1, message_id="m1", ts="2024-01-02T00:00:00+00:00")
        early = _email_item(conn, sid, iid, n=2, message_id="m2", ts="2024-01-01T00:00:00+00:00")
        undated = _email_item(conn, sid, iid, n=3, message_id="m3", ts=None)
        _email_item(conn, sid, iid, n=4, message_id="other", thread_key="other-tk")
        rows = list_thread_rows(conn, sid, "tk")
        assert [int(r["id"]) for r in rows] == [early, late, undated]
        return [late, early, undated]

    ctx.db.write(_go)


def test_resolve_parents_scales_with_replies_not_items(ctx: AppContext) -> None:
    """Real-data regression (126k-email mbox): the reconciliation must drive
    from the replies (partial index on in_reply_to), never correlate per item.
    The O(n^2) plan this guards against took >10 minutes at 126k; the fixed
    set-based pass is sub-second at this test's 20k."""
    import time

    def _seed(conn: sqlite3.Connection) -> None:
        sid = insert_source(conn)
        iid = insert_import(conn, sid)
        conn.executemany(
            """INSERT INTO items (source_id, import_id, kind, external_id, content_hash)
               VALUES (?, ?, 'email', ?, ?)""",
            [(sid, iid, f"e{n}", f"h{n}") for n in range(20_000)],
        )
        # satellite rows: every 100th message is a reply to the previous one
        conn.executemany(
            """INSERT INTO emails (item_id, message_id, in_reply_to, thread_key,
                                   to_json, cc_json, labels_json)
               VALUES (?, ?, ?, ?, '[]', '[]', '[]')""",
            [
                (
                    n + 1,
                    f"m{n}",
                    f"m{n - 1}" if n % 100 == 99 else None,
                    f"m{n - (n % 100)}",
                )
                for n in range(20_000)
            ],
        )

    ctx.db.write(_seed)

    def _resolve(conn: sqlite3.Connection) -> tuple[int, float]:
        started = time.perf_counter()
        resolved = resolve_email_parents(conn, 1)
        return resolved, time.perf_counter() - started

    resolved, elapsed = ctx.db.write(_resolve)
    assert resolved == 200
    assert elapsed < 5.0, f"parent resolution took {elapsed:.1f}s at 20k items — O(n^2) plan?"


def test_resolve_parents_plan_is_reply_driven(ctx: AppContext) -> None:
    """Pin the join order: the link subquery must start from the replies
    (partial in_reply_to index) and reach every other table by PK or the
    message_id index — NEVER a per-reply scan of items via the source index.
    (The planner chose exactly that without ANALYZE stats on a real 126k DB.)"""

    def _plan(conn: sqlite3.Connection) -> list[str]:
        insert_source(conn)
        rows = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT child.item_id AS child_id, MIN(pi.id) AS parent_item_id
            FROM emails AS child
            CROSS JOIN emails AS parent_sat
            CROSS JOIN items AS pi
            CROSS JOIN items AS ci
            WHERE child.in_reply_to IS NOT NULL
              AND parent_sat.message_id = child.in_reply_to
              AND pi.id = parent_sat.item_id
              AND ci.id = child.item_id
              AND ci.source_id = 1
              AND ci.parent_id IS NULL
              AND pi.source_id = ci.source_id
              AND pi.id != child.item_id
            GROUP BY child.item_id
            """
        ).fetchall()
        return [str(r[3]) for r in rows]

    plan = ctx.db.write(_plan)
    text = "\n".join(plan)
    # The driver is one linear pass over emails (SCAN child or the partial
    # in_reply_to index — both O(n) ONCE); everything downstream must be an
    # indexed lookup. "SCAN pi/ci/parent_sat" would mean per-reply table scans.
    assert plan[0].startswith(("SCAN child", "SEARCH child")), text
    assert "SEARCH parent_sat USING COVERING INDEX idx_emails_message_id" in text, text
    assert "SEARCH pi USING INTEGER PRIMARY KEY" in text, text
    assert "SEARCH ci USING INTEGER PRIMARY KEY" in text, text
