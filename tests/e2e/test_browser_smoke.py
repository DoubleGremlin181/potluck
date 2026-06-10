"""Browser smoke: ``potluck serve`` renders the stats page end to end.

Runs only with ``-m browser`` (excluded by default). Requires a built SPA
(``cd web && npm run build``) and Playwright chromium
(``playwright install chromium``).
"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIST = REPO_ROOT / "web" / "dist"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_healthy(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/api/health", timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise RuntimeError(f"server at {url} did not become healthy within {timeout}s")


@pytest.fixture
def server_url(tmp_path: Path) -> Iterator[str]:
    """A real `potluck serve` subprocess on a free port with a tmp database."""
    if not (WEB_DIST / "index.html").is_file():
        pytest.skip("web/dist missing — run `npm run build` in web/ first")
    port = _free_port()
    env = os.environ | {
        "POTLUCK_DB_PATH": str(tmp_path / "potluck.db"),
        "POTLUCK_WEB_DIST": str(WEB_DIST),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "potluck", "serve", "--no-browser", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_healthy(url)
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_stats_page_renders_zero_counts(server_url: str, page: Page) -> None:
    page.goto(server_url)
    expect(page.get_by_role("heading", name="Potluck")).to_be_visible()
    expect(page.get_by_text("Items")).to_be_visible()
    expect(page.get_by_text("Sources")).to_be_visible()
    expect(page.get_by_text("Database size")).to_be_visible()
    # The three count cards all show 0 on a fresh database.
    expect(page.get_by_text("0", exact=True)).to_have_count(3)
