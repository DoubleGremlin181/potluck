"""Locate and serve the built SPA across install modes."""

from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

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


class SPAStaticFiles(StaticFiles):
    """StaticFiles with a single-page-app fallback (#135).

    Client routes (``/items/5``, ``/settings``, …) exist only inside the SPA
    router, so a hard reload or shared deep link 404s under plain
    ``StaticFiles``. This subclass serves ``index.html`` (200) for GET/HEAD
    paths that miss the build directory, EXCEPT:

    - ``/api/*`` and ``/mcp*``: never SPA territory — unknown API paths keep
      the enveloped 404 (matched routes/mounts don't reach this app anyway;
      this guard covers the unmatched remainder, e.g. ``/api/nope``).
    - Paths whose final segment contains a dot (``/favicon-missing.png``): a
      missing asset must stay a 404, not silently become the app shell.
    """

    # Mounted at "/", so the path StaticFiles receives is the request path
    # without its leading slash.
    _EXCLUDED_PREFIXES = ("api/", "mcp")

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if (
                exc.status_code == 404
                and not path.startswith(self._EXCLUDED_PREFIXES)
                and "." not in path.rsplit("/", 1)[-1]
            ):
                return await super().get_response("index.html", scope)
            raise
