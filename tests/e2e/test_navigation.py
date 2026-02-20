"""Browser E2E tests for navigation, auth flow, and theme toggling."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser


class TestLoginFlow:
    """Test login page and authentication redirects."""

    def test_login_page_loads(self, page: Page, live_server: str) -> None:
        """Login page should render with a password input."""
        page.goto(f"{live_server}/login")
        expect(page.locator('input[type="password"]')).to_be_visible()

    def test_unauthenticated_redirects_to_login(self, page: Page, live_server: str) -> None:
        """Visiting / without auth should redirect to /login."""
        page.goto(f"{live_server}/")
        expect(page).to_have_url(f"{live_server}/login")


class TestAuthenticatedNavigation:
    """Test navigation with a valid auth cookie."""

    def test_dashboard_loads(self, authenticated_page: Page, live_server: str) -> None:
        """Dashboard should load for authenticated users."""
        authenticated_page.goto(f"{live_server}/")
        expect(authenticated_page.locator("body")).to_contain_text("Potluck")

    def test_theme_toggle(self, authenticated_page: Page, live_server: str) -> None:
        """Clicking the theme toggle should switch the data-theme attribute."""
        authenticated_page.goto(f"{live_server}/")
        html = authenticated_page.locator("html")

        initial_theme = html.get_attribute("data-theme")
        authenticated_page.click('button[aria-label="Toggle theme"]')
        new_theme = html.get_attribute("data-theme")

        assert initial_theme != new_theme
        assert new_theme in ("potluck-light", "potluck-dark")

    def test_nav_links_work(self, authenticated_page: Page, live_server: str) -> None:
        """Clicking nav links should navigate to the correct pages."""
        authenticated_page.goto(f"{live_server}/")

        # Navigate to Search
        authenticated_page.click('a[href="/search"]>> nth=0')
        authenticated_page.wait_for_url(f"{live_server}/search")

        # Navigate to Timeline
        authenticated_page.click('a[href="/timeline"] >> nth=0')
        authenticated_page.wait_for_url(f"{live_server}/timeline")

        # Navigate to Map
        authenticated_page.click('a[href="/map"] >> nth=0')
        authenticated_page.wait_for_url(f"{live_server}/map")
