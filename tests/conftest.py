"""Shared fixtures for the Potluck test suite.

Patterns established here are reused by every later phase:

- ``isolated_dirs`` (autouse): every test gets private platformdirs roots under
  ``tmp_path`` and a clean ``POTLUCK_*`` environment, so tests never touch real
  user data and are safe under pytest-xdist.
"""

import os
from pathlib import Path

import pytest


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
