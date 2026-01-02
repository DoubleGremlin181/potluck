"""Tests for logging configuration."""

import logging
from io import StringIO

from potluck.core.logging import get_logger, setup_logging


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
