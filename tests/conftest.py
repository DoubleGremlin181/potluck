"""Shared fixtures for the Potluck test suite.

Patterns established here are reused by every later phase:

- ``isolated_dirs`` (autouse): every test gets private platformdirs roots under
  ``tmp_path`` and a clean ``POTLUCK_*`` environment, so tests never touch real
  user data and are safe under pytest-xdist.
"""

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from potluck.api.app import create_app
from potluck.core.config import Settings
from potluck.services.context import AppContext, create_context

# ---------------------------------------------------------------------------
# Plain FK-scaffolding helpers (importable by any test module, reused across
# storage and ingest-layer tests).
# ---------------------------------------------------------------------------


def insert_source(conn: sqlite3.Connection, name: str = "test-src") -> int:
    """Insert a row into ``sources`` and return its rowid."""
    conn.execute("INSERT INTO sources (name) VALUES (?)", (name,))
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    return int(row[0])


def insert_import(conn: sqlite3.Connection, source_id: int) -> int:
    """Insert a row into ``imports`` and return its rowid."""
    conn.execute(
        """INSERT INTO imports (source_id, path, parser_version, started_at)
           VALUES (?, '/tmp/x', 1, '2024-01-01T00:00:00.000Z')""",
        (source_id,),
    )
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    return int(row[0])


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate platformdirs + POTLUCK_* env for each test.

    Potluck resolves all filesystem locations through the functions in
    ``potluck.core.paths`` at call time (never import-time constants), so
    patching the environment here is sufficient isolation.
    """
    for key in [k for k in os.environ if k.startswith("POTLUCK_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path


@pytest.fixture
def settings(isolated_dirs: Path) -> Settings:
    """Zero-config Settings resolving inside the isolated tmp dirs."""
    return Settings()


@pytest.fixture
def ctx(settings: Settings) -> Iterator[AppContext]:
    """AppContext on a fresh tmp-path SQLite database.

    This is THE fixture for service-layer tests (and everything above them):
    real Settings, real Database, fully isolated, closed on teardown.
    """
    context = create_context(settings)
    yield context
    context.db.close()


@pytest.fixture
def api_client(ctx: AppContext, tmp_path: Path) -> Iterator[TestClient]:
    """FastAPI TestClient over the ctx fixture (lifespan runs; no SPA build).

    ``web_dist`` is pinned to a nonexistent directory so the app is hermetic
    even when the repo has a real ``web/dist`` build lying around.
    """
    no_spa = AppContext(
        settings=ctx.settings.model_copy(update={"web_dist": tmp_path / "no-spa"}),
        db=ctx.db,
    )
    with TestClient(create_app(no_spa)) as client:
        yield client
