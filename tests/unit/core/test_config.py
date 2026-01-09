"""Tests for configuration settings."""

import os
from unittest.mock import patch

from potluck.core.config import Settings, get_settings


class TestSettings:
    """Tests for Settings configuration."""

    def test_default_values(self) -> None:
        """Settings have sensible defaults."""
        settings = Settings()
        assert "postgresql" in settings.database_url
        assert "redis" in settings.redis_url
        assert settings.log_level == "INFO"
        assert settings.web_port == 8000

    def test_env_override(self) -> None:
        """Settings can be overridden via environment variables."""
        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG", "WEB_PORT": "9000"}):
            # Clear cache to get fresh settings
            get_settings.cache_clear()
            settings = Settings()
            assert settings.log_level == "DEBUG"
            assert settings.web_port == 9000
        get_settings.cache_clear()

    def test_get_settings_cached(self) -> None:
        """get_settings returns cached instance."""
        get_settings.cache_clear()
        settings1 = get_settings()
        settings2 = get_settings()
        assert settings1 is settings2
        get_settings.cache_clear()
