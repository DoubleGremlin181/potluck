"""Root pytest configuration."""

from pathlib import Path

import pytest
from PIL import Image


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
        help="Run ML-dependent tests (requires torch, facenet-pytorch, etc.)",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "e2e: end-to-end tests requiring Docker")
    config.addinivalue_line(
        "markers", "ml: tests requiring ML dependencies (torch, facenet-pytorch, etc.)"
    )
    config.addinivalue_line("markers", "browser: browser E2E tests requiring Playwright")

    if config.getoption("--run-e2e"):
        # Include e2e tests but keep browser tests excluded (they must run
        # in a separate invocation due to Playwright/asyncio event loop conflict).
        config.option.markexpr = "not browser"


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Modify test collection based on markers and options."""
    if not config.getoption("--run-e2e"):
        skip_e2e = pytest.mark.skip(reason="Need --run-e2e option to run")
        for item in items:
            if item.get_closest_marker("e2e"):
                item.add_marker(skip_e2e)

    if not config.getoption("--run-ml"):
        skip_ml = pytest.mark.skip(reason="Need --run-ml option to run (or use Docker test env)")
        for item in items:
            if item.get_closest_marker("ml"):
                item.add_marker(skip_ml)


# =============================================================================
# Image fixtures for processing tests
# =============================================================================


@pytest.fixture(scope="session")
def sample_jpeg_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a sample JPEG image for testing."""
    tmp_dir = tmp_path_factory.mktemp("images")
    path = tmp_dir / "sample.jpg"

    img = Image.new("RGB", (100, 100), color=(255, 128, 64))
    for x in range(0, 100, 10):
        for y in range(0, 100, 10):
            img.putpixel((x, y), (x * 2, y * 2, 128))

    img.save(path, "JPEG", quality=95)
    return path


@pytest.fixture(scope="session")
def sample_png_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a sample PNG image for testing."""
    tmp_dir = tmp_path_factory.mktemp("images")
    path = tmp_dir / "sample.png"

    img = Image.new("RGB", (100, 100), color=(255, 128, 64))
    for x in range(0, 100, 10):
        for y in range(0, 100, 10):
            img.putpixel((x, y), (x * 2, y * 2, 128))

    img.save(path, "PNG")
    return path


@pytest.fixture(scope="session")
def identical_images_different_formats(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path]:
    """Create identical images in PNG and JPEG formats.

    Uses the real sample_face.jpg fixture as source, which produces
    more realistic perceptual hash behavior than synthetic patterns.
    """
    tmp_dir = tmp_path_factory.mktemp("identical")

    # Use real fixture image as source for realistic pHash behavior
    source_path = Path(__file__).parent / "fixtures" / "sample_face.jpg"
    img = Image.open(source_path)

    jpeg_path = tmp_dir / "image.jpg"
    png_path = tmp_dir / "image.png"

    img.save(jpeg_path, "JPEG", quality=95)
    img.save(png_path, "PNG")

    return jpeg_path, png_path


@pytest.fixture(scope="session")
def image_with_text() -> Path:
    """Return path to sample text image for OCR testing.

    Uses pre-generated fixture from tests/fixtures/.
    """
    fixture_path = Path(__file__).parent / "fixtures" / "sample_text.png"
    if not fixture_path.exists():
        raise FileNotFoundError(
            f"Test fixture not found: {fixture_path}\n"
            "Run: python tests/fixtures/generate_fixtures.py"
        )
    return fixture_path


@pytest.fixture(scope="session")
def image_with_face() -> Path:
    """Return path to sample face image for face detection testing.

    Uses pre-generated fixture from tests/fixtures/.
    Note: This is a synthetic face - real face detectors may not detect it.
    The test verifies the stage runs without error, not detection accuracy.
    """
    fixture_path = Path(__file__).parent / "fixtures" / "sample_face.jpg"
    if not fixture_path.exists():
        raise FileNotFoundError(
            f"Test fixture not found: {fixture_path}\n"
            "Run: python tests/fixtures/generate_fixtures.py"
        )
    return fixture_path


@pytest.fixture
def temp_media_path(tmp_path: Path) -> Path:
    """Create a temporary directory for media files."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    return media_dir


# =============================================================================
# Ingestion fixtures
# =============================================================================


@pytest.fixture(scope="session")
def google_takeout_fixtures_path() -> Path:
    """Return path to Google Takeout test fixtures.

    Provides a single source of truth for the fixtures path, eliminating
    the need for module-level FIXTURES_PATH constants in test files.
    """
    return Path(__file__).parent / "fixtures" / "google_takeout"
