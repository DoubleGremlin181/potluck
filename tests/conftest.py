"""Shared fixtures for the Potluck test suite.

Patterns established here are reused by every later phase:

- ``isolated_dirs`` (autouse): every test gets private platformdirs roots under
  ``tmp_path`` and a clean ``POTLUCK_*`` environment, so tests never touch real
  user data and are safe under pytest-xdist.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from potluck.api.app import create_app
from potluck.core.config import Settings
from potluck.services.context import AppContext, create_context


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
def api_client(ctx: AppContext) -> Iterator[TestClient]:
    """FastAPI TestClient over the ctx fixture (lifespan runs; no SPA build)."""
    with TestClient(create_app(ctx)) as client:
        yield client
