"""Shared fixtures for the Potluck test suite.

Patterns established here are reused by every later phase:

- ``isolated_dirs`` (autouse): every test gets private platformdirs roots under
  ``tmp_path`` and a clean ``POTLUCK_*`` environment, so tests never touch real
  user data and are safe under pytest-xdist.
"""

import os
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from potluck.api.app import create_app
from potluck.core.config import Settings
from potluck.services.context import AppContext, create_context
from potluck.services.imports import import_path
from potluck.testing.keep import write_keep_takeout

# ---------------------------------------------------------------------------
# Plain FK-scaffolding helpers (importable by any test module, reused across
# storage and ingest-layer tests).
# ---------------------------------------------------------------------------


def ingest_keep_corpus(ctx: AppContext, tmp_path: Path, count: int = 20, seed: int = 42) -> None:
    """Ingest a synthetic Keep corpus into *ctx*.

    Shared by any test module that needs a populated FTS corpus.  Uses a
    deterministic RNG seed so results are identical across runs.
    """
    archive = write_keep_takeout(tmp_path / "keep_takeout", count, seed=seed, fmt="dir")
    import_path(ctx, archive)


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


def insert_item(
    conn: sqlite3.Connection,
    source_id: int,
    import_id: int,
    *,
    content_hash: str,
    kind: str = "note",
    external_id: str | None = None,
    ts: str | None = None,
    title: str | None = None,
    text: str | None = None,
) -> int:
    """Insert a minimal ``items`` row and return its rowid.

    THE direct-SQL item helper for storage/search-layer tests (the ingest
    engine is deliberately bypassed; engine-level tests use run_import).
    """
    cursor = conn.execute(
        """INSERT INTO items (source_id, import_id, kind, external_id, content_hash,
                              ts, title, text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (source_id, import_id, kind, external_id, content_hash, ts, title, text),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


@pytest.fixture
def clean_registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Swap the plugin registry for an empty dict and empty the sources
    package path, so tests can register toy plugins via @source without real
    plugins (google_keep, …) leaking in through discover().

    monkeypatch restores both on teardown.
    """
    import potluck.ingest.plugins as plugins_mod
    import potluck.ingest.sources as sources_pkg

    fresh: dict[str, Any] = {}
    monkeypatch.setattr(plugins_mod, "_registry", fresh)
    monkeypatch.setattr(sources_pkg, "__path__", [])
    return fresh


@pytest.fixture
def isolated_sources(
    clean_registry: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Path]:
    """clean_registry plus module discovery rooted at tmp_path: write toy
    plugin modules into the returned directory and discover() imports them.

    Modules imported during the test are dropped from sys.modules on teardown
    so each test sees fresh import side effects.
    """
    import potluck.ingest.sources as sources_pkg

    before_modules = set(sys.modules.keys())
    monkeypatch.setattr(sources_pkg, "__path__", [str(tmp_path)])

    yield tmp_path

    for key in list(sys.modules.keys()):
        if key not in before_modules:
            del sys.modules[key]


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate platformdirs + POTLUCK_* env for each test.

    Potluck resolves all filesystem locations through the functions in
    ``potluck.core.paths`` at call time (never import-time constants), so
    patching the environment here is sufficient isolation.

    POTLUCK_DB_PATH is pinned explicitly (mirroring the Linux XDG layout, so
    it equals default_db_path() there): platformdirs' Windows backend ignores
    XDG_*, and env beats config.toml — without this, Windows tests would hit
    the user's real %LOCALAPPDATA% database.
    """
    for key in [k for k in os.environ if k.startswith("POTLUCK_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("POTLUCK_DB_PATH", str(tmp_path / "data" / "potluck" / "potluck.db"))
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
