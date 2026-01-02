"""Root pytest configuration."""

import pytest

# Import fixtures from fixtures directory
pytest_plugins = ["tests.fixtures.conftest"]


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add custom command line options."""
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="Run end-to-end tests (requires Docker)",
    )
    parser.addoption(
        "--run-ml",
        action="store_true",
        default=False,
        help="Run ML-dependent tests (requires torch, deepface, etc.)",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "e2e: end-to-end tests requiring Docker")
    config.addinivalue_line(
        "markers", "ml: tests requiring ML dependencies (torch, deepface, etc.)"
    )

    if config.getoption("--run-e2e"):
        # Remove the default marker filter when --run-e2e is specified
        config.option.markexpr = ""


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Modify test collection based on markers and options."""
    if not config.getoption("--run-e2e"):
        skip_e2e = pytest.mark.skip(reason="Need --run-e2e option to run")
        for item in items:
            if "e2e" in item.keywords:
                item.add_marker(skip_e2e)

    if not config.getoption("--run-ml"):
        skip_ml = pytest.mark.skip(reason="Need --run-ml option to run (or use Docker test env)")
        for item in items:
            if "ml" in item.keywords:
                item.add_marker(skip_ml)
