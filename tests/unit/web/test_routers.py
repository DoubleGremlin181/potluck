"""Tests for web router configuration."""

from potluck.web.routers import auth, dashboard, notes, search
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
