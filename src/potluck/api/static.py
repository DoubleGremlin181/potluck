"""Locate the built SPA across install modes."""

from pathlib import Path

import potluck
from potluck.core.config import Settings


def find_web_dist(settings: Settings) -> Path | None:
    """Resolve the SPA build directory, or None if no usable build exists.

    Order: explicit ``web_dist`` setting (authoritative — no silent fallback
    if it is broken) > build packaged into the wheel (``potluck/web_dist``) >
    repo checkout build (``web/dist``).
    """
    if settings.web_dist is not None:
        return settings.web_dist if (settings.web_dist / "index.html").is_file() else None
    packaged = Path(potluck.__file__).parent / "web_dist"
    for candidate in (packaged, Path("web") / "dist"):
        if (candidate / "index.html").is_file():
            return candidate
    return None
