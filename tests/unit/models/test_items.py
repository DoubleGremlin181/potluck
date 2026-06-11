"""Item DTOs and draft↔row mapping round-trip tests."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from potluck.models.drafts import NoteDraft
from potluck.models.items import Item, ItemKind
from potluck.storage.db import Database
from potluck.storage.items import draft_to_row, row_to_item
from tests.conftest import insert_import, insert_source

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_draft_to_row_to_item_roundtrip(tmp_path: Path) -> None:
    """Build a NoteDraft, convert to row, INSERT, SELECT, row_to_item → values preserved."""
    ts_in = datetime(2024, 6, 15, 12, 30, 0, 123456, tzinfo=UTC)
    draft = NoteDraft(
        ts=ts_in,
        title="My Note",
        text="Hello world",
        external_id="ext-001",
        lat=37.7749,
        lon=-122.4194,
        meta={"source": "test", "count": 42},
    )

    content_hash = "abc123deadbeef"
    db = Database.open(tmp_path / "roundtrip.db")
    try:
        src_id = db.write(insert_source)
        imp_id = db.write(lambda c: insert_import(c, src_id))

        row_data = draft_to_row(
            draft, source_id=src_id, import_id=imp_id, content_hash=content_hash
        )

        def insert_and_fetch(conn: sqlite3.Connection) -> sqlite3.Row:
            conn.execute(
                """INSERT INTO items
                       (source_id, import_id, kind, external_id, content_hash,
                        ts, title, text, lat, lon, parent_id, meta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                row_data,
            )
            rowid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            fetched = conn.execute("SELECT * FROM items WHERE id = ?", (rowid,)).fetchone()
            assert fetched is not None
            return cast(sqlite3.Row, fetched)

        row = db.write(insert_and_fetch)

        item = row_to_item(row, source_name="test-src")

        assert isinstance(item, Item)
        assert item.kind == ItemKind.NOTE
        assert item.source == "test-src"
        assert item.import_id == imp_id
        assert item.external_id == "ext-001"
        assert item.content_hash == content_hash
        assert item.title == "My Note"
        assert item.text == "Hello world"
        assert item.lat == pytest.approx(37.7749)
        assert item.lon == pytest.approx(-122.4194)
        assert item.parent_id is None
        assert item.meta == {"source": "test", "count": 42}
        # Timestamp is tz-aware and equal
        assert item.ts is not None
        assert item.ts.tzinfo is not None
        assert item.ts == ts_in
    finally:
        db.close()


def test_draft_to_row_rejects_non_finite_meta_floats() -> None:
    """NaN/Infinity in meta would serialize to literal NaN — invalid JSON that
    only explodes later at migration 002's json_valid CHECK. Fail fast with a
    clear ValueError at row construction instead."""
    draft = NoteDraft(title="n", text="t", meta={"weird": float("nan")})
    with pytest.raises(ValueError, match="[Oo]ut of range float"):
        draft_to_row(draft, source_id=1, import_id=1, content_hash="h")


def test_draft_to_row_none_fields(tmp_path: Path) -> None:
    """A minimal NoteDraft (all optional fields None) round-trips with Nones preserved."""
    draft = NoteDraft()

    content_hash = "minimal-hash"
    db = Database.open(tmp_path / "nones.db")
    try:
        src_id = db.write(insert_source)
        imp_id = db.write(lambda c: insert_import(c, src_id))

        row_data = draft_to_row(
            draft, source_id=src_id, import_id=imp_id, content_hash=content_hash
        )

        def insert_and_fetch(conn: sqlite3.Connection) -> sqlite3.Row:
            conn.execute(
                """INSERT INTO items
                       (source_id, import_id, kind, external_id, content_hash,
                        ts, title, text, lat, lon, parent_id, meta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                row_data,
            )
            rowid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            fetched = conn.execute("SELECT * FROM items WHERE id = ?", (rowid,)).fetchone()
            assert fetched is not None
            return cast(sqlite3.Row, fetched)

        row = db.write(insert_and_fetch)
        item = row_to_item(row, source_name="test-src")

        assert item.ts is None
        assert item.title is None
        assert item.text is None
        assert item.lat is None
        assert item.lon is None
        assert item.external_id is None
        assert item.parent_id is None
        assert item.meta == {}
    finally:
        db.close()


def test_note_draft_kind_fixed() -> None:
    """NoteDraft.kind defaults to ItemKind.NOTE and cannot be mutated (frozen model)."""
    draft = NoteDraft()
    assert draft.kind == ItemKind.NOTE

    with pytest.raises(ValidationError):
        draft.kind = ItemKind.EMAIL  # type: ignore[assignment]


def test_note_draft_naive_datetime_rejected() -> None:
    """Passing a naive datetime for ts raises pydantic.ValidationError."""
    naive = datetime(2024, 6, 15, 12, 30, 0)  # no tzinfo
    with pytest.raises(ValidationError):
        NoteDraft(ts=naive)
