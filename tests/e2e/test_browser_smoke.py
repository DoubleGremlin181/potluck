"""Browser smoke: ``potluck serve`` renders the SPA shell end to end.

Runs only with ``-m browser`` (excluded by default). Requires a built SPA
(``cd web && npm run build``) and Playwright chromium
(``playwright install chromium``).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import serve_app

pytestmark = pytest.mark.browser


@pytest.fixture
def server_url(tmp_path: Path) -> Iterator[str]:
    """A real `potluck serve` subprocess on a free port with a tmp database."""
    with serve_app(tmp_path / "potluck.db", tmp_path / "config") as url:
        yield url


def test_shell_renders_and_stats_reachable(server_url: str, page: Page) -> None:
    page.goto(server_url)
    expect(page.get_by_role("heading", name="Potluck")).to_be_visible()
    # Search is the front door; an empty database shows the idle hint.
    expect(page.get_by_test_id("empty-idle")).to_be_visible()
    # The P0 stats overview lives on the Settings page now.
    page.get_by_role("navigation", name="Primary").get_by_role("link", name="Settings").click()
    expect(page.get_by_text("Items")).to_be_visible()
    expect(page.get_by_text("Sources")).to_be_visible()
    expect(page.get_by_text("Database size")).to_be_visible()
    # The three count cards all show 0 on a fresh database.
    expect(page.get_by_text("0", exact=True)).to_have_count(3)
