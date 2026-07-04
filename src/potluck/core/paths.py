"""Filesystem locations for Potluck data and configuration.

These are functions, not module-level constants, so environment overrides
(e.g. ``XDG_DATA_HOME`` in tests) are honored at call time.
"""

from pathlib import Path

import platformdirs

_APP_NAME = "potluck"


def data_dir() -> Path:
    """Directory for user data; the SQLite database lives here."""
    return Path(platformdirs.user_data_dir(_APP_NAME, appauthor=False))


def config_dir() -> Path:
    """Directory for optional user configuration (``config.toml``)."""
    return Path(platformdirs.user_config_dir(_APP_NAME, appauthor=False))


def default_db_path() -> Path:
    """Default location of the Potluck database."""
    return data_dir() / "potluck.db"


def default_attachments_dir() -> Path:
    """Default managed directory for extracted attachment blobs (#124)."""
    return data_dir() / "attachments"


def default_uploads_dir() -> Path:
    """Default managed directory for archives uploaded through the API (#132)."""
    return data_dir() / "uploads"
