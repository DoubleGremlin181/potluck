"""Fixtures for integration tests using Docker."""

import os
import subprocess
import time
from collections.abc import Generator

import psycopg2
import pytest


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
    """Database credentials from .env file."""
    env_file = os.path.join(project_root, ".env")
    env_example = os.path.join(project_root, ".env.example")

    # Use .env if exists, otherwise fall back to .env.example
    env_path = env_file if os.path.exists(env_file) else env_example
    env_vars = load_env_file(env_path)

    return {
        "host": "localhost",
        "port": int(env_vars.get("POSTGRES_PORT", "5432")),
        "user": env_vars.get("POSTGRES_USER", "potluck"),
        "password": env_vars.get("POSTGRES_PASSWORD", "changeme_in_production"),
        "dbname": env_vars.get("POSTGRES_DB", "potluck"),
    }


@pytest.fixture(scope="session")
def docker_compose_up(
    project_root: str,
    db_credentials: dict[str, str | int],
) -> Generator[None, None, None]:
    """Start docker-compose services for testing using setup.sh script.

    This fixture:
    1. Runs scripts/setup.sh which handles .env creation and service startup
    2. Waits for the database to be ready
    3. Yields control to tests
    4. Tears down containers after tests complete
    """
    setup_script = os.path.join(project_root, "scripts", "setup.sh")

    # Run the setup script with --db-only and --non-interactive flags for testing
    result = subprocess.run(
        ["bash", setup_script, "--db-only", "--non-interactive"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        pytest.fail(f"Setup script failed:\nstdout: {result.stdout}\nstderr: {result.stderr}")

    # Verify database is ready (setup.sh should have already done this, but double-check)
    db_ready = wait_for_db(
        host=str(db_credentials["host"]),
        port=int(db_credentials["port"]),
        user=str(db_credentials["user"]),
        password=str(db_credentials["password"]),
        dbname=str(db_credentials["dbname"]),
    )

    if not db_ready:
        # Get logs for debugging
        logs = subprocess.run(
            ["docker", "compose", "logs", "db"],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        # Clean up and fail
        subprocess.run(
            ["docker", "compose", "down", "-v"],
            cwd=project_root,
            check=False,
        )
        pytest.fail(f"Database did not become ready in time.\nLogs:\n{logs.stdout}\n{logs.stderr}")

    yield

    # Teardown: stop and remove containers and volumes
    subprocess.run(
        ["docker", "compose", "down", "-v"],
        cwd=project_root,
        check=False,
    )


@pytest.fixture(scope="session")
def db_connection(
    docker_compose_up: None,  # noqa: ARG001 - ensures Docker is running
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
    docker_compose_up: None,  # noqa: ARG001 - ensures Docker is running
) -> None:
    """Migrations are run by setup.sh, this fixture just ensures Docker is up."""
    # setup.sh already runs migrations via: docker compose exec app alembic upgrade head
    pass
