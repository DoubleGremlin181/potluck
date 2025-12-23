"""Root pytest configuration."""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add custom command line options."""
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="Run end-to-end tests (requires Docker)",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Configure pytest based on command line options."""
    if config.getoption("--run-e2e"):
        # Remove the default marker filter when --run-e2e is specified
        # This allows e2e tests to run
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
