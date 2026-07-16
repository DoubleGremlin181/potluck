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


def test_db_path_isolated_even_when_platformdirs_ignores_xdg(
    isolated_dirs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """platformdirs' Windows backend ignores XDG_DATA_HOME, so the test-suite
    isolation must not rely on it — otherwise every ctx fixture on Windows
    would resolve to the user's real %LOCALAPPDATA% database."""
    import platformdirs

    monkeypatch.setattr(
        platformdirs, "user_data_dir", lambda *a, **k: "/real/windows/userdata"
    )  # simulate XDG being ignored
    assert Settings().db_path.is_relative_to(isolated_dirs)


def test_attachment_settings_defaults() -> None:
    """#124: extraction is opt-in; the managed dir lives under the data dir."""
    from potluck.core.paths import data_dir

    settings = Settings()
    assert settings.extract_attachments is False
    assert settings.attachments_dir == data_dir() / "attachments"


def test_gdrive_settings_defaults_and_flat_toml_keys(isolated_dirs: Path) -> None:
    """#152: off until a client is supplied; prune hard-defaults off; the
    config surface is FLAT gdrive_* keys (the watch_* family shape — a TOML
    [gdrive] section would not map onto pydantic-settings fields)."""
    from potluck.core.paths import data_dir

    settings = Settings()
    assert settings.gdrive_client_id is None
    assert settings.gdrive_client_secret is None
    assert settings.gdrive_folder_name == "Takeout"
    assert settings.gdrive_interval_s == 86400.0
    assert settings.gdrive_downloads_dir == data_dir() / "gdrive"
    assert settings.gdrive_prune is False
    assert settings.gdrive_enabled is True

    cfg = config_dir()
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text(
        'gdrive_client_id = "cid.apps.googleusercontent.com"\n'
        'gdrive_client_secret = "GOCSPX-secret"\n'
        "gdrive_prune = true\n"
        "gdrive_interval_s = 3600\n"
    )
    loaded = Settings()
    assert loaded.gdrive_client_id == "cid.apps.googleusercontent.com"
    assert loaded.gdrive_client_secret == "GOCSPX-secret"
    assert loaded.gdrive_prune is True
    assert loaded.gdrive_interval_s == 3600.0
