"""Allow ``python -m potluck`` (used by tests spawning the real server)."""

from potluck.cli.app import app

if __name__ == "__main__":
    app()
