"""Hatchling build hook: bundle the SPA build into the wheel when present.

``web/dist`` exists when CI/release builds the frontend first, so release
wheels ship the SPA at ``potluck/web_dist``. Plain ``uvx --from git+...``
installs build without it and the API serves a friendly "build missing"
message instead (see ``potluck.api.static.find_web_dist``).
"""

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class WebDistBuildHook(BuildHookInterface):
    PLUGIN_NAME = "web-dist"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        dist = Path(self.root) / "web" / "dist"
        if (dist / "index.html").is_file():
            build_data["force_include"][str(dist)] = "potluck/web_dist"
