"""Dev service: thin wrappers over ingest.devtools for the CLI/API layer.

The interface layers (cli, api, mcp) import this module rather than reaching
into ingest.devtools directly, keeping the import-linter contract intact.
"""

from pathlib import Path

from potluck.ingest.devtools import check_source as _check_source
from potluck.ingest.devtools import new_source as _new_source


def new_source(name: str) -> Path:
    """Scaffold a new source plugin module and return its path."""
    return _new_source(name)


def check_source(name: str) -> list[str]:
    """Validate the source plugin named *name*; empty list means OK."""
    return _check_source(name)
