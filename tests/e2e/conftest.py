"""Shared helpers for the browser tier: real ``potluck serve`` subprocesses.

Browser tests drive the built SPA (``web/dist``) served by a real server
process over a private tmp database — never TestClient, never real user data.
"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIST = REPO_ROOT / "web" / "dist"


def api_get(url: str, path: str, **params: Any) -> dict[str, Any]:
    """Ground-truth JSON straight from the API (assertions only, never actions)."""
    resp = httpx.get(f"{url}{path}", params=params, timeout=10.0)
    resp.raise_for_status()
    return dict(resp.json())


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_healthy(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/api/health", timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise RuntimeError(f"server at {url} did not become healthy within {timeout}s")


@contextmanager
def serve_app(db_path: Path, config_home: Path) -> Iterator[str]:
    """A ``potluck serve`` subprocess on a free port over *db_path*.

    The environment is scrubbed of ``POTLUCK_*`` so tests never inherit the
    developer's real database, and ``XDG_CONFIG_HOME`` is pinned so a real
    ``config.toml`` cannot leak in either.
    """
    if not (WEB_DIST / "index.html").is_file():
        pytest.skip("web/dist missing — run `npm run build` in web/ first")
    port = free_port()
    env = {key: value for key, value in os.environ.items() if not key.startswith("POTLUCK_")}
    env["POTLUCK_DB_PATH"] = str(db_path)
    env["POTLUCK_WEB_DIST"] = str(WEB_DIST)
    env["XDG_CONFIG_HOME"] = str(config_home)
    proc = subprocess.Popen(
        [sys.executable, "-m", "potluck", "serve", "--no-browser", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        wait_healthy(url)
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)
