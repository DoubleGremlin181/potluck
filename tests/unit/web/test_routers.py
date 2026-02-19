"""Tests for web router configuration."""

from potluck.web.routers import auth, dashboard


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
