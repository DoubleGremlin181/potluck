"""Page Object Model for the Map page."""

from playwright.sync_api import Page


class MapPage:
    """Encapsulates interactions with the /map page."""

    def __init__(self, page: Page, base_url: str) -> None:
        self.page = page
        self.base_url = base_url

    def navigate(self) -> None:
        """Navigate to /map and wait for the map container to be visible."""
        self.page.goto(f"{self.base_url}/map")
        self.page.wait_for_selector("#map-container", state="visible")

    def get_marker_count_text(self) -> str:
        """Return the text content of the marker count element."""
        el = self.page.wait_for_selector("#marker-count")
        assert el is not None
        return el.text_content() or ""

    def wait_for_markers_loaded(self) -> str:
        """Wait until the marker count text contains 'markers loaded'."""
        self.page.wait_for_function(
            "() => document.getElementById('marker-count')?.textContent?.includes('markers loaded')"
        )
        return self.get_marker_count_text()

    def click_first_marker(self) -> None:
        """Click the first visible marker on the map."""
        self.page.click(".potluck-marker >> nth=0")

    def get_popup_content(self) -> str:
        """Return the text content of the currently open popup."""
        popup = self.page.wait_for_selector(".leaflet-popup-content", state="visible")
        assert popup is not None
        return popup.text_content() or ""

    def toggle_type_filter(self, entity_type: str, *, checked: bool) -> None:
        """Toggle a type filter checkbox by its value attribute."""
        cb = self.page.locator(f'.map-type-filter[value="{entity_type}"]')
        if cb.is_checked() != checked:
            cb.click()

    def set_date_range(self, since: str = "", until: str = "") -> None:
        """Fill date inputs and trigger the Filter button."""
        if since:
            self.page.fill("#map-since", since)
        if until:
            self.page.fill("#map-until", until)
        self.page.click("button:has-text('Filter')")
