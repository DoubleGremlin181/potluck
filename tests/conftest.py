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
    """Create identical images in PNG and JPEG formats."""
    tmp_dir = tmp_path_factory.mktemp("identical")

    img = Image.new("RGB", (200, 200), color=(100, 150, 200))
    for x in range(200):
        for y in range(200):
            if (x + y) % 20 < 10:
                img.putpixel((x, y), (255, 200, 100))

    jpeg_path = tmp_dir / "image.jpg"
    png_path = tmp_dir / "image.png"

    img.save(jpeg_path, "JPEG", quality=95)
    img.save(png_path, "PNG")

    return jpeg_path, png_path


@pytest.fixture(scope="session")
def image_with_text(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create an image with text for OCR testing."""
    from PIL import ImageDraw

    tmp_dir = tmp_path_factory.mktemp("ocr")
    path = tmp_dir / "text_image.png"

    img = Image.new("RGB", (400, 100), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((10, 30), "Hello World 12345", fill=(0, 0, 0))

    img.save(path, "PNG")
    return path


@pytest.fixture(scope="session")
def image_with_face(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a simple face-like image for testing."""
    from PIL import ImageDraw

    tmp_dir = tmp_path_factory.mktemp("faces")
    path = tmp_dir / "face_like.png"

    img = Image.new("RGB", (200, 200), color=(200, 200, 200))
    draw = ImageDraw.Draw(img)

    draw.ellipse([50, 30, 150, 170], fill=(255, 220, 180))
    draw.ellipse([70, 70, 90, 90], fill=(50, 50, 50))
    draw.ellipse([110, 70, 130, 90], fill=(50, 50, 50))
    draw.arc([80, 100, 120, 140], start=0, end=180, fill=(150, 50, 50), width=2)

    img.save(path, "PNG")
    return path


@pytest.fixture
def temp_media_path(tmp_path: Path) -> Path:
    """Create a temporary directory for media files."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    return media_dir
