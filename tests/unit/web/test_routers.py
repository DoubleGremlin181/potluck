"""Tests for web router configuration."""

from potluck.web.routers import (
    auth,
    dashboard,
    events,
    imports,
    notes,
    people,
    search,
    settings,
    timeline,
)
from potluck.web.routers import map as map_router
from potluck.web.routers import media as media_router


def _route_paths(router: object) -> set[str]:
    """Extract all route paths from a router."""
    return {r.path for r in router.routes if hasattr(r, "path")}  # type: ignore[attr-defined]


class TestRouterConfiguration:
    """Test that all routers are properly configured."""

    def test_auth_router_has_routes(self) -> None:
        """Auth router should have login/logout routes."""
        paths = _route_paths(auth.router)
        assert "/login" in paths
        assert "/logout" in paths

    def test_dashboard_router_has_root(self) -> None:
        """Dashboard router should handle /."""
        paths = _route_paths(dashboard.router)
        assert "/" in paths

    def test_search_router_has_search(self) -> None:
        """Search router should handle /search."""
        paths = _route_paths(search.router)
        assert "/search" in paths

    def test_media_router_has_routes(self) -> None:
        """Media router should have gallery and detail routes."""
        paths = _route_paths(media_router.router)
        assert "/media" in paths
        assert "/media/{media_id}/detail" in paths

    def test_notes_router_has_crud(self) -> None:
        """Notes router should have list, create, edit, delete."""
        paths = _route_paths(notes.router)
        assert "/notes" in paths
        assert "/notes/{note_id}/edit" in paths
        assert "/notes/{note_id}/delete" in paths

    def test_people_router_has_routes(self) -> None:
        """People router should have list, detail, merge."""
        paths = _route_paths(people.router)
        assert "/people" in paths
        assert "/people/{person_id}" in paths
        assert "/people/merge" in paths
        assert "/people/{person_id}/alias" in paths

    def test_timeline_router_has_items_endpoint(self) -> None:
        """Timeline router should have page and items endpoints."""
        paths = _route_paths(timeline.router)
        assert "/timeline" in paths
        assert "/timeline/items" in paths

    def test_imports_router_has_routes(self) -> None:
        """Imports router should have upload, start, cancel, browse."""
        paths = _route_paths(imports.router)
        assert "/imports" in paths
        assert "/imports/upload" in paths
        assert "/imports/start" in paths
        assert "/imports/{run_id}/cancel" in paths
        assert "/imports/browse" in paths

    def test_events_router_has_sse(self) -> None:
        """Events router should have SSE progress endpoint."""
        paths = _route_paths(events.router)
        assert "/events/progress" in paths

    def test_settings_router_has_page(self) -> None:
        """Settings router should handle /settings."""
        paths = _route_paths(settings.router)
        assert "/settings" in paths

    def test_map_router_has_routes(self) -> None:
        """Map router should have page and markers endpoints."""
        paths = _route_paths(map_router.router)
        assert "/map" in paths
        assert "/map/markers" in paths
