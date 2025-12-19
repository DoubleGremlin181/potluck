"""Tests for core infrastructure."""

import logging
import os
from io import StringIO
from unittest.mock import patch

import pytest

from potluck.core.config import Settings, get_settings
from potluck.core.exceptions import (
    ConfigurationError,
    DatabaseError,
    EntityNotFoundError,
    IngestionError,
    PotluckError,
    ProcessingError,
)
from potluck.core.logging import get_logger, setup_logging


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


class TestLogging:
    """Tests for logging configuration."""

    def test_setup_logging_returns_logger(self) -> None:
        """setup_logging returns a configured logger."""
        stream = StringIO()
        logger = setup_logging(level="DEBUG", stream=stream)
        assert isinstance(logger, logging.Logger)
        assert logger.name == "potluck"
        assert logger.level == logging.DEBUG

    def test_get_logger_namespaced(self) -> None:
        """get_logger returns namespaced logger."""
        logger = get_logger("test.module")
        assert logger.name == "potluck.test.module"

    def test_logging_output_format(self) -> None:
        """Logs are formatted correctly."""
        stream = StringIO()
        setup_logging(level="INFO", stream=stream)
        logger = get_logger("test")
        logger.info("Test message")
        output = stream.getvalue()
        assert "INFO" in output
        assert "potluck.test" in output
        assert "Test message" in output


class TestExceptions:
    """Tests for custom exceptions."""

    def test_potluck_error_base(self) -> None:
        """PotluckError stores message."""
        error = PotluckError("test error")
        assert error.message == "test error"
        assert str(error) == "test error"

    def test_configuration_error(self) -> None:
        """ConfigurationError inherits from PotluckError."""
        error = ConfigurationError("bad config")
        assert isinstance(error, PotluckError)
        assert error.message == "bad config"

    def test_database_error(self) -> None:
        """DatabaseError inherits from PotluckError."""
        error = DatabaseError("db failed")
        assert isinstance(error, PotluckError)

    def test_entity_not_found_error(self) -> None:
        """EntityNotFoundError includes entity info."""
        error = EntityNotFoundError("User", 123)
        assert error.entity_type == "User"
        assert error.entity_id == 123
        assert "User" in str(error)
        assert "123" in str(error)

    def test_ingestion_error(self) -> None:
        """IngestionError inherits from PotluckError."""
        error = IngestionError("import failed")
        assert isinstance(error, PotluckError)

    def test_processing_error(self) -> None:
        """ProcessingError inherits from PotluckError."""
        error = ProcessingError("processing failed")
        assert isinstance(error, PotluckError)

    def test_exceptions_catchable_as_potluck_error(self) -> None:
        """All custom exceptions can be caught as PotluckError."""
        exceptions = [
            ConfigurationError("msg"),
            DatabaseError("msg"),
            EntityNotFoundError("Type", 1),
            IngestionError("msg"),
            ProcessingError("msg"),
        ]
        for exc in exceptions:
            with pytest.raises(PotluckError):
                raise exc
