"""Browser tests for the settings-page watch-folders card (#151).

Real ``potluck serve`` over a tmp database with watch folders configured via
``config.toml`` (the folder list is config-file-owned; the UI only displays
it). The enable toggle drives the real PATCH /api/watch — no interception —
and persistence is asserted through both the API and a full page reload.

Runs only with ``-m browser``; requires ``web/dist`` and Playwright chromium.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, Response, expect

from tests.e2e.conftest import api_get, serve_app

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def watch_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path, Path]]:
    """A served app with two watch folders (one present, one missing);
    yields (url, present_folder, missing_folder)."""
    root = tmp_path_factory.mktemp("watch-fixture")
    present = root / "watched"
    present.mkdir()
    missing = root / "not-there"
    config_home = root / "config"
    config_file = config_home / "potluck" / "config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        f"watch_folders = [{str(present)!r}, {str(missing)!r}]\nwatch_interval_s = 0.2\n"
    )
    with serve_app(root / "potluck.db", config_home) as url:
        yield url, present, missing


def test_watch_card_renders_folders_and_badges(
    watch_server: tuple[str, Path, Path], page: Page
) -> None:
    url, present, missing = watch_server
    page.goto(f"{url}/settings")

    toggle = page.get_by_test_id("watch-toggle")
    expect(toggle).to_be_visible()
    expect(toggle).to_be_checked()  # config default: enabled

    present_row = page.locator(f'[data-testid="watch-folder"][data-path="{present}"]')
    expect(present_row).to_be_visible()
    expect(present_row).to_have_attribute("data-exists", "true")
    expect(present_row).to_contain_text("watching")

    missing_row = page.locator(f'[data-testid="watch-folder"][data-path="{missing}"]')
    expect(missing_row).to_have_attribute("data-exists", "false")
    expect(missing_row).to_contain_text("missing")

    # Folder list and interval are config-file-owned: the card says so and
    # shows the live interval instead of offering edits.
    card = page.get_by_test_id("watch-card")
    expect(card).to_contain_text("config.toml")
    expect(card).to_contain_text("0.2")
    # Auto-imports surface on the imports page — the card links to it.
    expect(card.get_by_role("link", name="Imports")).to_have_attribute("href", "/imports")


def test_toggle_persists_via_real_patch(watch_server: tuple[str, Path, Path], page: Page) -> None:
    url, _, _ = watch_server
    page.goto(f"{url}/settings")

    def _is_patch(resp: Response) -> bool:
        return resp.url.endswith("/api/watch") and resp.request.method == "PATCH"

    toggle = page.get_by_test_id("watch-toggle")
    expect(toggle).to_be_checked()
    # Wait for the REAL PATCH (no interception) so the ground-truth read
    # below cannot race the optimistic update.
    with page.expect_response(_is_patch) as patched:
        toggle.click()
    assert patched.value.ok
    expect(toggle).not_to_be_checked()

    status = api_get(url, "/api/watch")
    assert status["enabled"] is False
    assert status["effective_enabled_source"] == "runtime"

    # Survives a full reload (server state, not component state).
    page.reload()
    expect(page.get_by_test_id("watch-toggle")).not_to_be_checked()

    # Toggle back on for neighbouring tests.
    with page.expect_response(_is_patch) as repatched:
        page.get_by_test_id("watch-toggle").click()
    assert repatched.value.ok
    expect(page.get_by_test_id("watch-toggle")).to_be_checked()
    assert api_get(url, "/api/watch")["enabled"] is True
