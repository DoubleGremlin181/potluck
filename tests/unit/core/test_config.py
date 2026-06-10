"""Config precedence (env > config.toml > defaults) and zero-config startup."""

from pathlib import Path

import pytest

from potluck.core.config import Settings
from potluck.core.paths import config_dir, default_db_path


def test_defaults_with_no_config_present(isolated_dirs: Path) -> None:
    """Fresh machine: no config files, no env — everything has a usable default."""
    settings = Settings()
    assert settings.db_path == default_db_path()
    assert settings.db_path.is_relative_to(isolated_dirs)
    assert settings.host == "127.0.0.1"
    assert settings.port == 8765
    assert settings.web_dist is None


def test_toml_overrides_defaults(isolated_dirs: Path) -> None:
    cfg = config_dir()
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text('port = 9000\nhost = "0.0.0.0"\n')
    settings = Settings()
    assert settings.port == 9000
    assert settings.host == "0.0.0.0"


def test_env_overrides_toml(isolated_dirs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config_dir()
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text("port = 9000\n")
    monkeypatch.setenv("POTLUCK_PORT", "9001")
    assert Settings().port == 9001


def test_env_overrides_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POTLUCK_DB_PATH", str(tmp_path / "custom.db"))
    assert Settings().db_path == tmp_path / "custom.db"
