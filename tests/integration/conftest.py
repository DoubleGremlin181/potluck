"""Fixtures for integration tests using Docker.

This module provides fixtures that work in two environments:
1. Local development: Uses docker-compose via scripts/setup.sh
2. CI (GitHub Actions): Uses pre-configured PostgreSQL service

The CI environment is detected via the CI environment variable.
"""

import os
import subprocess
import time
from collections.abc import Generator

import psycopg2
import pytest


def is_ci() -> bool:
    """Check if running in CI environment."""
    return os.environ.get("CI", "").lower() == "true"


def wait_for_db(
    host: str,
    port: int,
    user: str,
    password: str,
    dbname: str,
    max_retries: int = 60,
    retry_interval: float = 2.0,
) -> bool:
    """Wait for PostgreSQL database to be ready.

    Args:
        host: Database host
        port: Database port
        user: Database user
        password: Database password
        dbname: Database name
        max_retries: Maximum number of connection attempts
        retry_interval: Seconds between retries

    Returns:
        True if database is ready, False if max retries exceeded
    """
    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                dbname=dbname,
                connect_timeout=5,
            )
            conn.close()
            return True
        except psycopg2.OperationalError:
            if attempt < max_retries - 1:
                time.sleep(retry_interval)
    return False


def load_env_file(env_path: str) -> dict[str, str]:
    """Load environment variables from a .env file."""
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env_vars[key.strip()] = value.strip()
    return env_vars


@pytest.fixture(scope="session")
def project_root() -> str:
    """Path to project root directory."""
    return os.path.join(os.path.dirname(__file__), "..", "..")


@pytest.fixture(scope="session")
def db_credentials(project_root: str) -> dict[str, str | int]:
    """Database credentials from .env file or environment."""
    env_file = os.path.join(project_root, ".env")
    env_example = os.path.join(project_root, ".env.example")

    # Use .env if exists, otherwise fall back to .env.example
    env_path = env_file if os.path.exists(env_file) else env_example
    env_vars = load_env_file(env_path)

    return {
        "host": env_vars.get("POSTGRES_HOST", "localhost"),
        "port": int(env_vars.get("POSTGRES_PORT", "5432")),
        "user": env_vars.get("POSTGRES_USER", "potluck"),
        "password": env_vars.get("POSTGRES_PASSWORD", "changeme_in_production"),
        "dbname": env_vars.get("POSTGRES_DB", "potluck"),
    }


@pytest.fixture(scope="session")
def ensure_db_available(
    project_root: str,
    db_credentials: dict[str, str | int],
) -> Generator[None, None, None]:
    """Ensure database is available for testing.

    In CI: Database is already running via GitHub Actions services.
    Locally: Starts docker-compose via scripts/setup.sh.
    """
    if is_ci():
        # In CI, database is already available via GitHub Actions services
        # Just verify it's ready
        db_ready = wait_for_db(
            host=str(db_credentials["host"]),
            port=int(db_credentials["port"]),
            user=str(db_credentials["user"]),
            password=str(db_credentials["password"]),
            dbname=str(db_credentials["dbname"]),
            max_retries=30,
            retry_interval=1.0,
        )
        if not db_ready:
            pytest.fail("Database not available in CI environment")
        yield
    else:
        # Local development: use docker-compose via setup.sh
        setup_script = os.path.join(project_root, "scripts", "setup.sh")

        result = subprocess.run(
            ["bash", setup_script, "--db-only", "--non-interactive"],
            cwd=project_root,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            pytest.fail(f"Setup script failed:\nstdout: {result.stdout}\nstderr: {result.stderr}")

        db_ready = wait_for_db(
            host=str(db_credentials["host"]),
            port=int(db_credentials["port"]),
            user=str(db_credentials["user"]),
            password=str(db_credentials["password"]),
            dbname=str(db_credentials["dbname"]),
        )

        if not db_ready:
            logs = subprocess.run(
                ["docker", "compose", "logs", "db"],
                cwd=project_root,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["docker", "compose", "down", "-v"],
                cwd=project_root,
                check=False,
            )
            pytest.fail(
                f"Database did not become ready in time.\nLogs:\n{logs.stdout}\n{logs.stderr}"
            )

        yield

        # Teardown: stop containers only in local development
        subprocess.run(
            ["docker", "compose", "down", "-v"],
            cwd=project_root,
            check=False,
        )


# Keep docker_compose_up as an alias for backward compatibility
@pytest.fixture(scope="session")
def docker_compose_up(
    ensure_db_available: None,  # noqa: ARG001
) -> Generator[None, None, None]:
    """Alias for ensure_db_available (backward compatibility)."""
    yield


@pytest.fixture(scope="session")
def db_connection(
    ensure_db_available: None,  # noqa: ARG001 - ensures database is running
    db_credentials: dict[str, str | int],
) -> Generator[psycopg2.extensions.connection, None, None]:
    """Create a database connection for tests."""
    conn = psycopg2.connect(
        host=str(db_credentials["host"]),
        port=int(db_credentials["port"]),
        user=str(db_credentials["user"]),
        password=str(db_credentials["password"]),
        dbname=str(db_credentials["dbname"]),
    )
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def run_migrations(
    ensure_db_available: None,  # noqa: ARG001 - ensures database is running
    project_root: str,
    db_credentials: dict[str, str | int],
) -> None:
    """Run database migrations.

    In CI: Migrations are run by the workflow before tests.
    Locally: Migrations are run by setup.sh.
    """
    # In both cases, migrations should already be applied
    # This fixture just ensures the database is up and ready
    pass
