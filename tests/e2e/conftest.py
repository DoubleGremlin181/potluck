"""Shared fixtures for browser E2E tests.

Browser tests auto-skip when prerequisites are missing:
- Playwright Chromium not installed → skip at collection time
- Database/Redis unavailable → live_server fixture fails to start → skip
"""

import multiprocessing
import time
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest
from itsdangerous import URLSafeTimedSerializer
from playwright.sync_api import Page

# Fixed credentials for E2E tests — only used inside the child server process.
_E2E_PASSWORD = "e2e-test-password"
_E2E_SECRET_KEY = "e2e-test-secret-key"


def _chromium_installed() -> bool:
    """Check if Playwright Chromium browser is installed."""
    try:
        cache_dir = Path.home() / ".cache" / "ms-playwright"
        return cache_dir.exists() and any(
            d.name.startswith("chromium") for d in cache_dir.iterdir()
        )
    except Exception:
        return False


# Auto-skip all browser tests in this directory if Chromium is not installed.
if not _chromium_installed():
    pytestmark = pytest.mark.skip(
        reason="Playwright Chromium not installed (run: playwright install chromium)"
    )


def _run_server() -> None:
    """Start the FastAPI app in a child process.

    Sets WEB_PASSWORD and WEB_SECRET_KEY inside the child only, so the
    parent process's os.environ and settings cache are never polluted.
    """
    import os

    import uvicorn

    from potluck.core.config import get_settings

    os.environ["WEB_PASSWORD"] = _E2E_PASSWORD
    os.environ["WEB_SECRET_KEY"] = _E2E_SECRET_KEY

    # Clear the lru_cache so the child process picks up the env vars above
    # instead of reusing stale cached values inherited from the parent.
    get_settings.cache_clear()

    uvicorn.run(
        "potluck.web.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8765,
        log_level="warning",
    )


@pytest.fixture(scope="session")
def live_server() -> Generator[str, None, None]:
    """Start FastAPI app in a subprocess and wait until it accepts connections.

    Skips all browser tests if the server cannot start (e.g. no database).
    """
    proc = multiprocessing.Process(target=_run_server, daemon=True)
    proc.start()

    base = "http://127.0.0.1:8765"
    for _ in range(60):
        try:
            httpx.get(f"{base}/login", timeout=2.0)
            break
        except httpx.ConnectError:
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.skip("Live server did not start (database/Redis unavailable?)")

    yield base
    proc.kill()


@pytest.fixture
def authenticated_page(page: Page, live_server: str) -> Page:
    """Playwright Page with a valid auth cookie pre-set."""
    serializer = URLSafeTimedSerializer(_E2E_SECRET_KEY)
    token = serializer.dumps("authenticated")
    page.context.add_cookies(
        [
            {
                "name": "session_token",
                "value": token,
                "url": live_server,
            }
        ]
    )
    return page
