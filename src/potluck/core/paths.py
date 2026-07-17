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


def default_gdrive_downloads_dir() -> Path:
    """Default managed landing directory for Drive-pulled Takeout archives (#152).

    Watched by the #151 folder watcher when the Drive puller is configured —
    the puller only downloads; the watcher imports.
    """
    return data_dir() / "gdrive"


def gdrive_token_path() -> Path:
    """The Google OAuth token file (#152): refresh token + granted scopes.

    Written 0600 and never stored in the database (see
    docs/decisions/gdrive-takeout-pull.md §3).
    """
    return config_dir() / "gdrive_token.json"
