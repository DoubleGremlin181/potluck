"""Core infrastructure for Potluck."""

from potluck.core.cli import app as cli_app
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

__all__ = [
    # CLI
    "cli_app",
    # Config
    "Settings",
    "get_settings",
    # Logging
    "setup_logging",
    "get_logger",
    # Exceptions
    "PotluckError",
    "ConfigurationError",
    "DatabaseError",
    "EntityNotFoundError",
    "IngestionError",
    "ProcessingError",
]
