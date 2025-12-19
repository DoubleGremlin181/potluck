"""Standardized logging configuration."""

import logging
import sys
from typing import TextIO

from potluck.core.config import get_settings


def setup_logging(
    level: str | None = None,
    stream: TextIO = sys.stderr,
) -> logging.Logger:
    """Configure and return the root potluck logger.

    Args:
        level: Log level override. Defaults to settings.log_level.
        stream: Output stream. Defaults to stderr.

    Returns:
        Configured logger instance.
    """
    settings = get_settings()
    log_level = level or settings.log_level

    # Create formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Create handler
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)

    # Configure root potluck logger
    logger = logging.getLogger("potluck")
    logger.setLevel(log_level.upper())
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name under the potluck namespace.

    Args:
        name: Logger name (will be prefixed with 'potluck.').

    Returns:
        Logger instance.
    """
    return logging.getLogger(f"potluck.{name}")
