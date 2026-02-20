"""Browser E2E tests for the Imports page."""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.pages.imports_page import ImportsPage

pytestmark = pytest.mark.browser


class TestImportsPageLoad:
    """Test that the imports page loads correctly."""

    def test_imports_page_loads(self, authenticated_page: Page, live_server: str) -> None:
        """Imports page should have an upload form and active imports div."""
        ip = ImportsPage(authenticated_page, live_server)
        ip.navigate()
        expect(authenticated_page.locator("#active-imports")).to_be_attached()
        # Page should have a file upload form
        expect(authenticated_page.locator('input[type="file"]')).to_be_attached()


class TestImportsHTMXPolling:
    """Test HTMX polling behavior on the imports page."""

    def test_active_imports_has_polling_attributes(
        self, authenticated_page: Page, live_server: str
    ) -> None:
        """The #active-imports div should have hx-get and hx-trigger for polling."""
        ip = ImportsPage(authenticated_page, live_server)
        ip.navigate()

        div = ip.get_active_imports_div()
        expect(div).to_have_attribute("hx-get", "/imports/active")
        expect(div).to_have_attribute("hx-trigger", "every 3s")

    def test_htmx_poll_fires_request(self, authenticated_page: Page, live_server: str) -> None:
        """HTMX should fire a polling request to /imports/active within ~3s."""
        ip = ImportsPage(authenticated_page, live_server)
        ip.navigate()

        # Wait for the HTMX poll to fire (hx-trigger="every 3s")
        ip.wait_for_htmx_poll(timeout=5000)
