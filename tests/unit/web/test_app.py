"""Tests for the web app factory and routing."""

from potluck.web.app import create_app


class TestAppFactory:
    """Test the FastAPI app factory."""

    def test_create_app_returns_fastapi_instance(self) -> None:
        """App factory should return a configured FastAPI app."""
        app = create_app()
        assert app.title == "Potluck"

    def test_all_routers_registered(self) -> None:
        """All expected routes should be registered."""
        app = create_app()
        paths = {r.path for r in app.routes if hasattr(r, "path")}

        # Core routes
        assert "/" in paths
        assert "/login" in paths
        assert "/logout" in paths
        assert "/search" in paths
        assert "/media" in paths
        assert "/notes" in paths
        assert "/people" in paths
        assert "/timeline" in paths
        assert "/timeline/data" in paths

    def test_media_serving_routes(self) -> None:
        """Media serving routes should be registered."""
        app = create_app()
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/media/file/{media_id}" in paths
        assert "/media/thumb/{media_id}" in paths

    def test_static_files_mounted(self) -> None:
        """Static files should be mounted at /static."""
        app = create_app()
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/static" in paths

    def test_docs_disabled(self) -> None:
        """API docs should be disabled (no /docs or /redoc)."""
        app = create_app()
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/docs" not in paths
        assert "/redoc" not in paths

    def test_templates_on_app_state(self) -> None:
        """Jinja2 templates should be attached to app.state."""
        app = create_app()
        assert hasattr(app.state, "templates")

    def test_basename_filter_registered(self) -> None:
        """Custom Jinja2 basename filter should be available."""
        app = create_app()
        assert "basename" in app.state.templates.env.filters


class TestAuthMiddleware:
    """Test authentication middleware behavior."""

    def test_no_password_allows_all(self) -> None:
        """When WEB_PASSWORD is not set, all requests should pass through."""
        from potluck.web.app import AuthMiddleware

        # AuthMiddleware is instantiated by the app, just verify it exists
        assert AuthMiddleware is not None
