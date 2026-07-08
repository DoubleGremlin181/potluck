"""FTS5 external-content index: triggers, BM25 ranking, integrity."""

import sqlite3
from pathlib import Path

import pytest

from potluck.core.errors import FtsIntegrityError
from potluck.storage import fts
from potluck.storage.db import Database, connect
from potluck.storage.migrate import apply_migrations
from tests.conftest import insert_import, insert_item, insert_source

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_conn(tmp_path: Path, name: str = "fts.db") -> sqlite3.Connection:
    conn = connect(tmp_path / name)
    apply_migrations(conn)
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_insert_makes_row_searchable(tmp_path: Path) -> None:
    """INSERT on items fires the ai trigger; MATCH finds the row."""
    conn = _open_conn(tmp_path)
    src = insert_source(conn)
    imp = insert_import(conn, src)
    rowid = insert_item(conn, src, imp, title="hello world", content_hash="hash-1")
    rows = conn.execute(
        "SELECT rowid FROM items_fts WHERE items_fts MATCH ?", ("hello",)
    ).fetchall()
    assert any(int(r[0]) == rowid for r in rows)
    conn.close()


def test_delete_removes_from_index(tmp_path: Path) -> None:
    """DELETE on items fires the ad trigger; MATCH no longer finds the row."""
    conn = _open_conn(tmp_path)
    src = insert_source(conn)
    imp = insert_import(conn, src)
    rowid = insert_item(conn, src, imp, title="farewell world", content_hash="hash-2")
    conn.execute("DELETE FROM items WHERE id = ?", (rowid,))
    rows = conn.execute(
        "SELECT rowid FROM items_fts WHERE items_fts MATCH ?", ("farewell",)
    ).fetchall()
    assert not rows
    fts.integrity_check(conn)  # passes: delete trigger used correct old values
    conn.close()


def test_update_reindexes(tmp_path: Path) -> None:
    """UPDATE on items fires the au trigger; old token gone, new token found."""
    conn = _open_conn(tmp_path)
    src = insert_source(conn)
    imp = insert_import(conn, src)
    rowid = insert_item(conn, src, imp, text="oldtoken", content_hash="hash-3")
    conn.execute("UPDATE items SET text = 'newtoken' WHERE id = ?", (rowid,))
    old_rows = conn.execute(
        "SELECT rowid FROM items_fts WHERE items_fts MATCH ?", ("oldtoken",)
    ).fetchall()
    assert not old_rows
    new_rows = conn.execute(
        "SELECT rowid FROM items_fts WHERE items_fts MATCH ?", ("newtoken",)
    ).fetchall()
    assert any(int(r[0]) == rowid for r in new_rows)
    fts.integrity_check(conn)
    conn.close()


@pytest.mark.parametrize(
    "title,text,content_hash",
    [
        (None, "something", "hash-null-title"),
        ("something", None, "hash-null-text"),
    ],
)
def test_null_column_roundtrip(
    tmp_path: Path, title: str | None, text: str | None, content_hash: str
) -> None:
    """NULL title or text: insert + delete must not corrupt the FTS index (external-content NULL gotcha)."""
    conn = _open_conn(tmp_path)
    src = insert_source(conn)
    imp = insert_import(conn, src)
    rowid = insert_item(conn, src, imp, title=title, text=text, content_hash=content_hash)
    conn.execute("DELETE FROM items WHERE id = ?", (rowid,))
    fts.integrity_check(conn)  # must not raise
    conn.close()


def test_integrity_check_detects_desync(tmp_path: Path) -> None:
    """Dropping triggers then mutating items causes FtsIntegrityError; rebuild heals it."""
    conn = _open_conn(tmp_path)
    src = insert_source(conn)
    imp = insert_import(conn, src)

    # Insert one item normally so the FTS index has content.
    insert_item(conn, src, imp, title="syncedrow", content_hash="hash-sync")

    # Drop triggers so subsequent mutations bypass the FTS index.
    conn.execute("DROP TRIGGER items_fts_ai")
    conn.execute("DROP TRIGGER items_fts_ad")
    conn.execute("DROP TRIGGER items_fts_au")

    # Insert a new item — FTS index won't know about it.
    insert_item(conn, src, imp, title="ghostrow", content_hash="hash-ghost")

    with pytest.raises(FtsIntegrityError):
        fts.integrity_check(conn)

    # Rebuild heals the index.
    fts.rebuild(conn)
    fts.integrity_check(conn)  # must not raise

    # Rebuild must restore searchability, not just structural consistency.
    rows = conn.execute(
        "SELECT rowid FROM items_fts WHERE items_fts MATCH ?", ("ghostrow",)
    ).fetchall()
    assert rows, "rebuild must restore searchability of the previously-ghosted row"
    conn.close()


def test_title_outranks_text(tmp_path: Path) -> None:
    """BM25 title weight (5.0) ranks title-match above text-match."""
    conn = _open_conn(tmp_path)
    src = insert_source(conn)
    imp = insert_import(conn, src)
    rowid_a = insert_item(
        conn, src, imp, title="garnet crystal", text="plain text", content_hash="hash-a"
    )
    insert_item(
        conn, src, imp, title="unrelated heading", text="garnet mention", content_hash="hash-b"
    )
    rows = conn.execute(
        "SELECT rowid FROM items_fts WHERE items_fts MATCH ? ORDER BY bm25(items_fts, ?, ?)",
        ("garnet", fts.BM25_WEIGHT_TITLE, fts.BM25_WEIGHT_TEXT),
    ).fetchall()
    assert rows, "no results for 'garnet'"
    assert int(rows[0][0]) == rowid_a, "title match should outrank text match"
    conn.close()


def test_prefix_query_matches(tmp_path: Path) -> None:
    """Prefix search 'gar*' finds both title and text rows (prefix index functional check)."""
    conn = _open_conn(tmp_path)
    src = insert_source(conn)
    imp = insert_import(conn, src)
    insert_item(conn, src, imp, title="garnet crystal", content_hash="hash-p1")
    insert_item(conn, src, imp, text="garnet mention", content_hash="hash-p2")
    rows = conn.execute("SELECT rowid FROM items_fts WHERE items_fts MATCH ?", ("gar*",)).fetchall()
    assert len(rows) == 2
    conn.close()


def test_migration_idempotent_at_v3(tmp_path: Path) -> None:
    """Database.open() applies all migrations; user_version == latest (11)."""
    db = Database.open(tmp_path / "v3.db")
    try:
        with db.read() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        assert version == 11
    finally:
        db.close()
