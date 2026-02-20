"""Page Object Model for the Imports page."""

from playwright.sync_api import Locator, Page


class ImportsPage:
    """Encapsulates interactions with the /imports page."""

    def __init__(self, page: Page, base_url: str) -> None:
        self.page = page
        self.base_url = base_url

    def navigate(self) -> None:
        """Navigate to /imports and wait for the page to load."""
        self.page.goto(f"{self.base_url}/imports")
        self.page.wait_for_selector("#active-imports", state="attached")

    def get_active_imports_div(self) -> Locator:
        """Return the #active-imports element locator."""
        return self.page.locator("#active-imports")

    def wait_for_htmx_poll(self, *, timeout: float = 5000) -> None:
        """Wait for at least one HTMX poll request to /imports/active."""
        with self.page.expect_response(lambda r: "/imports/active" in r.url, timeout=timeout):
            pass  # The response will arrive from HTMX's periodic polling
