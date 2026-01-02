"""Shared test fixtures for processing tests.

These fixtures provide sample images and test data for processing tests.
Images are generated programmatically to avoid storing binary files in git.
"""

from pathlib import Path

import pytest
from PIL import Image

FIXTURES_DIR = Path(__file__).parent
IMAGES_DIR = FIXTURES_DIR / "images"


def _ensure_images_dir() -> Path:
    """Ensure the images directory exists."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    return IMAGES_DIR


@pytest.fixture(scope="session")
def sample_jpeg_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a sample JPEG image for testing.

    Returns path to a simple colored image saved as JPEG.
    """
    tmp_dir = tmp_path_factory.mktemp("images")
    path = tmp_dir / "sample.jpg"

    # Create a simple 100x100 RGB image with some color variation
    img = Image.new("RGB", (100, 100), color=(255, 128, 64))
    # Add some variation to make it more realistic
    for x in range(0, 100, 10):
        for y in range(0, 100, 10):
            img.putpixel((x, y), (x * 2, y * 2, 128))

    img.save(path, "JPEG", quality=95)
    return path


@pytest.fixture(scope="session")
def sample_png_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a sample PNG image for testing.

    Returns path to a simple colored image saved as PNG.
    """
    tmp_dir = tmp_path_factory.mktemp("images")
    path = tmp_dir / "sample.png"

    # Create same image as JPEG for perceptual hash comparison
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

    Useful for testing perceptual hash matching across formats.
    Returns tuple of (jpeg_path, png_path).
    """
    tmp_dir = tmp_path_factory.mktemp("identical")

    # Create a more complex image for better hash testing
    img = Image.new("RGB", (200, 200), color=(100, 150, 200))
    # Add a pattern
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
    """Create an image with text for OCR testing.

    Returns path to an image containing readable text.
    """
    from PIL import ImageDraw

    tmp_dir = tmp_path_factory.mktemp("ocr")
    path = tmp_dir / "text_image.png"

    # Create white background
    img = Image.new("RGB", (400, 100), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Add text (uses default font)
    text = "Hello World 12345"
    draw.text((10, 30), text, fill=(0, 0, 0))

    img.save(path, "PNG")
    return path


@pytest.fixture(scope="session")
def image_with_face(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a simple image that might trigger face detection.

    Note: This is a simple oval shape, real face detection tests
    should use actual face images from a test dataset.
    """
    from PIL import ImageDraw

    tmp_dir = tmp_path_factory.mktemp("faces")
    path = tmp_dir / "face_like.png"

    # Create image with face-like oval
    img = Image.new("RGB", (200, 200), color=(200, 200, 200))
    draw = ImageDraw.Draw(img)

    # Draw an oval (face-like shape)
    draw.ellipse([50, 30, 150, 170], fill=(255, 220, 180))
    # Eyes
    draw.ellipse([70, 70, 90, 90], fill=(50, 50, 50))
    draw.ellipse([110, 70, 130, 90], fill=(50, 50, 50))
    # Mouth
    draw.arc([80, 100, 120, 140], start=0, end=180, fill=(150, 50, 50), width=2)

    img.save(path, "PNG")
    return path


@pytest.fixture
def temp_media_path(tmp_path: Path) -> Path:
    """Create a temporary directory for media files."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    return media_dir
