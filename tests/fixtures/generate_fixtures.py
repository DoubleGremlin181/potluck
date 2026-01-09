#!/usr/bin/env python3
"""Generate test fixture images.

This script creates sample images for testing ML processing stages.
Run once to populate the fixtures directory.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def generate_text_image(output_path: Path) -> None:
    """Generate a clear text image for OCR testing."""
    img = Image.new("RGB", (600, 200), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Try to use a standard font
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:\\Windows\\Fonts\\arial.ttf",
    ]
    loaded_font: ImageFont.FreeTypeFont | None = None
    for font_path in font_paths:
        try:
            loaded_font = ImageFont.truetype(font_path, 48)
            break
        except OSError:
            continue

    font = loaded_font if loaded_font is not None else ImageFont.load_default()

    draw.text((30, 50), "Hello World", fill=(0, 0, 0), font=font)
    draw.text((30, 120), "Test 12345", fill=(0, 0, 0), font=font)

    img.save(output_path, "PNG")
    print(f"Created: {output_path}")


def generate_face_image(output_path: Path) -> None:
    """Download Obama portrait from Wikipedia (public domain)."""
    import urllib.request

    # Official White House photo - public domain (US government work)
    url = "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8d/President_Barack_Obama.jpg/480px-President_Barack_Obama.jpg"

    print(f"Downloading face image from {url}...")
    try:
        urllib.request.urlretrieve(url, output_path)
        print(f"Created: {output_path}")
    except Exception as e:
        print(f"Failed to download: {e}")
        raise


def main() -> None:
    """Generate all fixture images."""
    fixtures_dir = Path(__file__).parent

    generate_text_image(fixtures_dir / "sample_text.png")
    generate_face_image(fixtures_dir / "sample_face.jpg")

    print("Done!")


if __name__ == "__main__":
    main()
