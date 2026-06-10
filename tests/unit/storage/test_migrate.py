"""Migration runner: idempotency, atomic rollback, validation."""

from pathlib import Path

import pytest

from potluck.core.errors import MigrationError
from potluck.storage.db import connect
from potluck.storage.migrate import apply_migrations


def _table_exists(path: Path, name: str) -> bool:
    conn = connect(path)
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone()
        return row is not None
    finally:
        conn.close()


def test_builtin_migrations_apply_then_rerun_is_noop(tmp_path: Path) -> None:
    conn = connect(tmp_path / "m.db")
    try:
        assert apply_migrations(conn) == 2
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'meta'").fetchone()
        assert apply_migrations(conn) == 2  # idempotent no-op
    finally:
        conn.close()


def test_partial_failure_rolls_back_entire_migration(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_good.sql").write_text("CREATE TABLE a (x INTEGER);")
    (migrations / "002_bad.sql").write_text(
        "CREATE TABLE b (x INTEGER);\nINSERT INTO does_not_exist VALUES (1);"
    )
    db_path = tmp_path / "m.db"
    conn = connect(db_path)
    try:
        with pytest.raises(MigrationError, match="002_bad"):
            apply_migrations(conn, migrations)
        # 001 committed; 002 rolled back atomically (its CREATE TABLE undone).
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        conn.close()
    assert _table_exists(db_path, "a")
    assert not _table_exists(db_path, "b")


def test_duplicate_migration_numbers_rejected(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_one.sql").write_text("CREATE TABLE a (x INTEGER);")
    (migrations / "001_other.sql").write_text("CREATE TABLE b (x INTEGER);")
    conn = connect(tmp_path / "m.db")
    try:
        with pytest.raises(MigrationError, match="duplicate"):
            apply_migrations(conn, migrations)
    finally:
        conn.close()
