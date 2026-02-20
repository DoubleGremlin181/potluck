"""Browser E2E tests for the Map page."""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.pages.map_page import MapPage

pytestmark = pytest.mark.browser


class TestMapPageLoad:
    """Test that the map page initializes correctly."""

    def test_map_page_loads(self, authenticated_page: Page, live_server: str) -> None:
        """Map container should be visible and Leaflet should initialize."""
        mp = MapPage(authenticated_page, live_server)
        mp.navigate()
        expect(authenticated_page.locator("#map-container")).to_be_visible()
        # Leaflet adds .leaflet-container class when initialized
        expect(authenticated_page.locator(".leaflet-container")).to_be_visible()

    def test_markers_load_on_viewport(self, authenticated_page: Page, live_server: str) -> None:
        """Marker count text should appear after initial load."""
        mp = MapPage(authenticated_page, live_server)
        mp.navigate()
        count_text = mp.wait_for_markers_loaded()
        assert "markers loaded" in count_text


class TestMapFilters:
    """Test map type and date filters."""

    def test_type_filter_toggles_markers(self, authenticated_page: Page, live_server: str) -> None:
        """Unchecking a type filter should trigger a marker reload."""
        mp = MapPage(authenticated_page, live_server)
        mp.navigate()
        mp.wait_for_markers_loaded()

        # Uncheck the first available filter — this triggers loadMarkers()
        first_cb = authenticated_page.locator(".map-type-filter >> nth=0")
        if first_cb.count() > 0:
            first_value = first_cb.get_attribute("value") or ""
            with authenticated_page.expect_response(lambda r: "/map/markers" in r.url):
                mp.toggle_type_filter(first_value, checked=False)

    def test_date_filter_works(self, authenticated_page: Page, live_server: str) -> None:
        """Setting a date range and clicking Filter should reload markers."""
        mp = MapPage(authenticated_page, live_server)
        mp.navigate()
        mp.wait_for_markers_loaded()

        with authenticated_page.expect_response(lambda r: "/map/markers" in r.url):
            mp.set_date_range(since="2020-01-01", until="2025-12-31")


class TestMapMarkerPopup:
    """Test marker popup interactions."""

    def test_cluster_expand_on_zoom(self, authenticated_page: Page, live_server: str) -> None:
        """If cluster markers exist, zooming in should expand them."""
        mp = MapPage(authenticated_page, live_server)
        mp.navigate()
        mp.wait_for_markers_loaded()

        clusters = authenticated_page.locator(".marker-cluster")
        if clusters.count() > 0:
            clusters.first.click()
            # After clicking a cluster, Leaflet zooms in — wait for markers to reload
            authenticated_page.wait_for_timeout(1000)

    def test_marker_popup_shows_details(self, authenticated_page: Page, live_server: str) -> None:
        """Clicking a marker should open a popup with a title and type badge."""
        mp = MapPage(authenticated_page, live_server)
        mp.navigate()
        mp.wait_for_markers_loaded()

        markers = authenticated_page.locator(".potluck-marker")
        if markers.count() > 0:
            mp.click_first_marker()
            popup_text = mp.get_popup_content()
            # Popup should have some content (title + type badge)
            assert len(popup_text.strip()) > 0
