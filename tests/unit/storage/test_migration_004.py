"""Migration 004: one logical item per (source, external_id).

Covers the dedupe step (pre-004 DBs may hold several rows per pair from the
insert-only era), the partial unique index, and the imports.items_updated
counter column.
"""

import sqlite3
from importlib.resources import files
from pathlib import Path

import pytest

from potluck.storage import fts
from potluck.storage.db import connect
from potluck.storage.migrate import apply_migrations
from tests.conftest import insert_import, insert_item, insert_source


def _migrations_through_003(tmp_path: Path) -> Path:
    """Copy the built-in 001–003 migrations into a tmp dir (pre-004 schema)."""
    target = tmp_path / "migrations_003"
    target.mkdir()
    for item in (files("potluck.storage") / "migrations").iterdir():
        if item.name[:3] in ("001", "002", "003"):
            (target / item.name).write_text(item.read_text(encoding="utf-8"))
    return target


def test_unique_index_rejects_duplicate_source_external_id(tmp_path: Path) -> None:
    conn = connect(tmp_path / "m.db")
    try:
        apply_migrations(conn)
        src = insert_source(conn)
        imp = insert_import(conn, src)
        insert_item(
            conn, source_id=src, import_id=imp, external_id="Keep/a.json", content_hash="h1"
        )
        with pytest.raises(sqlite3.IntegrityError):
            insert_item(
                conn, source_id=src, import_id=imp, external_id="Keep/a.json", content_hash="h2"
            )
    finally:
        conn.close()


def test_null_external_ids_are_not_constrained(tmp_path: Path) -> None:
    conn = connect(tmp_path / "m.db")
    try:
        apply_migrations(conn)
        src = insert_source(conn)
        imp = insert_import(conn, src)
        insert_item(conn, source_id=src, import_id=imp, external_id=None, content_hash="h1")
        insert_item(conn, source_id=src, import_id=imp, external_id=None, content_hash="h2")
        count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        assert count == 2
    finally:
        conn.close()


def test_same_external_id_allowed_across_sources(tmp_path: Path) -> None:
    conn = connect(tmp_path / "m.db")
    try:
        apply_migrations(conn)
        src_a = insert_source(conn, "src-a")
        src_b = insert_source(conn, "src-b")
        imp_a = insert_import(conn, src_a)
        imp_b = insert_import(conn, src_b)
        insert_item(conn, source_id=src_a, import_id=imp_a, external_id="x", content_hash="h1")
        insert_item(conn, source_id=src_b, import_id=imp_b, external_id="x", content_hash="h2")
    finally:
        conn.close()


def test_dedupe_keeps_newest_row_and_fts_stays_consistent(tmp_path: Path) -> None:
    """A pre-004 DB with several rows per (source, external_id) keeps MAX(id)."""
    conn = connect(tmp_path / "m.db")
    try:
        assert apply_migrations(conn, _migrations_through_003(tmp_path)) == 3
        src = insert_source(conn)
        imp = insert_import(conn, src)
        # Insert-only era: three versions of the same note, plus unaffected rows.
        insert_item(
            conn, source_id=src, import_id=imp, external_id="dup", content_hash="d1", text="old"
        )
        insert_item(
            conn, source_id=src, import_id=imp, external_id="dup", content_hash="d2", text="mid"
        )
        survivor = insert_item(
            conn, source_id=src, import_id=imp, external_id="dup", content_hash="d3", text="new"
        )
        other = insert_item(
            conn, source_id=src, import_id=imp, external_id="other", content_hash="o1"
        )
        null_a = insert_item(
            conn, source_id=src, import_id=imp, external_id=None, content_hash="n1"
        )
        null_b = insert_item(
            conn, source_id=src, import_id=imp, external_id=None, content_hash="n2"
        )
        conn.commit()

        assert apply_migrations(conn) == 5

        ids = {int(r[0]) for r in conn.execute("SELECT id FROM items").fetchall()}
        assert ids == {survivor, other, null_a, null_b}
        # The FTS delete trigger fired for the removed rows.
        fts.integrity_check(conn)
        stale = conn.execute(
            "SELECT rowid FROM items_fts WHERE items_fts MATCH 'old OR mid'"
        ).fetchall()
        assert stale == []
    finally:
        conn.close()


def test_items_updated_defaults_to_zero_on_pre_004_rows(tmp_path: Path) -> None:
    conn = connect(tmp_path / "m.db")
    try:
        apply_migrations(conn, _migrations_through_003(tmp_path))
        src = insert_source(conn)
        imp = insert_import(conn, src)
        conn.commit()

        apply_migrations(conn)

        row = conn.execute("SELECT items_updated FROM imports WHERE id = ?", (imp,)).fetchone()
        assert int(row[0]) == 0
    finally:
        conn.close()
