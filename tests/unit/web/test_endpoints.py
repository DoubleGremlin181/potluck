"""Behavioral tests for web endpoints using HTTP client."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from potluck.models.base import EntityType
from potluck.web.routers.map import _extract_marker_extras


class TestAuthMiddleware:
    """Test authentication middleware dispatch logic."""

    async def test_unauthenticated_request_redirects_to_login(self, client: AsyncClient) -> None:
        """Unauthenticated request to protected route should redirect to /login."""
        with patch("potluck.web.app.get_settings") as mock_settings:
            mock_settings.return_value.web_password = "secret"
            mock_settings.return_value.web_secret_key = "test-key"
            response = await client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    async def test_no_password_allows_all(self, client: AsyncClient) -> None:
        """When WEB_PASSWORD is not set, all requests should pass through."""
        with patch("potluck.web.app.get_settings") as mock_settings:
            mock_settings.return_value.web_password = None
            response = await client.get("/", follow_redirects=False)
        assert response.status_code == 200

    async def test_login_page_accessible_without_auth(self, client: AsyncClient) -> None:
        """Login page should be accessible without authentication."""
        with patch("potluck.web.app.get_settings") as mock_settings:
            mock_settings.return_value.web_password = "secret"
            mock_settings.return_value.web_secret_key = "test-key"
            response = await client.get("/login", follow_redirects=False)
        # Should not redirect - should serve the page or redirect to /
        assert response.status_code != 303 or response.headers.get("location") != "/login"

    async def test_static_accessible_without_auth(self, client: AsyncClient) -> None:
        """Static files should be accessible without authentication."""
        with patch("potluck.web.app.get_settings") as mock_settings:
            mock_settings.return_value.web_password = "secret"
            mock_settings.return_value.web_secret_key = "test-key"
            response = await client.get("/static/js/theme.js", follow_redirects=False)
        # Should not redirect to login
        assert response.status_code != 303 or response.headers.get("location") != "/login"

    async def test_invalid_token_redirects(self, app: FastAPI) -> None:
        """Invalid session token should redirect to login."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"session_token": "invalid-token"},
        ) as invalid_client:
            with patch("potluck.web.app.get_settings") as mock_settings:
                mock_settings.return_value.web_password = "secret"
                mock_settings.return_value.web_secret_key = "test-key"
                response = await invalid_client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


class TestLoginEndpoint:
    """Test the login POST handler."""

    async def test_wrong_password_returns_401(self, client: AsyncClient) -> None:
        """Wrong password should return 401 with error."""
        with patch("potluck.web.routers.auth.get_settings") as mock_settings:
            mock_settings.return_value.web_password = "correct-password"
            mock_settings.return_value.web_secret_key = "test-key"
            response = await client.post(
                "/login",
                data={"password": "wrong-password"},
                follow_redirects=False,
            )
        assert response.status_code == 401

    async def test_correct_password_redirects_and_sets_cookie(self, client: AsyncClient) -> None:
        """Correct password should redirect to / and set session cookie."""
        with patch("potluck.web.routers.auth.get_settings") as mock_settings:
            mock_settings.return_value.web_password = "correct-password"
            mock_settings.return_value.web_secret_key = "test-key"
            response = await client.post(
                "/login",
                data={"password": "correct-password"},
                follow_redirects=False,
            )
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert "session_token" in response.cookies


class TestLogout:
    """Test the logout handler."""

    async def test_logout_clears_cookie(self, authed_client: AsyncClient) -> None:
        """Logout should redirect to login and clear session cookie."""
        response = await authed_client.get("/logout", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


class TestDashboard:
    """Test the dashboard endpoint."""

    async def test_dashboard_returns_200(self, authed_client: AsyncClient) -> None:
        """Dashboard should return 200 with entity counts."""
        response = await authed_client.get("/")
        assert response.status_code == 200
        assert "Potluck" in response.text


class TestNotesEndpoints:
    """Test notes CRUD endpoints."""

    async def test_notes_list_returns_200(self, authed_client: AsyncClient) -> None:
        """Notes list should return 200."""
        response = await authed_client.get("/notes")
        assert response.status_code == 200

    async def test_edit_nonexistent_note_returns_404(self, authed_client: AsyncClient) -> None:
        """Editing a non-existent note should return 404."""
        fake_id = uuid4()
        response = await authed_client.post(
            f"/notes/{fake_id}/edit",
            data={"content": "updated"},
            follow_redirects=False,
        )
        assert response.status_code == 404

    async def test_delete_nonexistent_note_returns_404(self, authed_client: AsyncClient) -> None:
        """Deleting a non-existent note should return 404."""
        fake_id = uuid4()
        response = await authed_client.post(
            f"/notes/{fake_id}/delete",
            follow_redirects=False,
        )
        assert response.status_code == 404

    async def test_edit_invalid_uuid_returns_422(self, authed_client: AsyncClient) -> None:
        """Invalid UUID should return 422, not 500."""
        response = await authed_client.post(
            "/notes/not-a-uuid/edit",
            data={"content": "test"},
            follow_redirects=False,
        )
        assert response.status_code == 422


class TestPeopleEndpoints:
    """Test people endpoints."""

    async def test_people_list_returns_200(self, authed_client: AsyncClient) -> None:
        """People list should return 200."""
        response = await authed_client.get("/people")
        assert response.status_code == 200

    async def test_person_detail_not_found(self, authed_client: AsyncClient) -> None:
        """Non-existent person should return 404."""
        fake_id = uuid4()
        response = await authed_client.get(f"/people/{fake_id}")
        assert response.status_code == 404

    async def test_person_invalid_uuid_returns_422(self, authed_client: AsyncClient) -> None:
        """Invalid UUID for person detail should return 422."""
        response = await authed_client.get("/people/not-a-uuid")
        assert response.status_code == 422

    async def test_merge_source_not_found_returns_404(self, authed_client: AsyncClient) -> None:
        """Merging with non-existent source should return 404."""
        response = await authed_client.post(
            "/people/merge",
            data={
                "source_id": str(uuid4()),
                "target_id": str(uuid4()),
            },
            follow_redirects=False,
        )
        assert response.status_code == 404


class TestMediaEndpoints:
    """Test media endpoints."""

    async def test_media_gallery_returns_200(self, authed_client: AsyncClient) -> None:
        """Media gallery should return 200."""
        response = await authed_client.get("/media")
        assert response.status_code == 200

    async def test_media_detail_invalid_uuid(self, authed_client: AsyncClient) -> None:
        """Invalid UUID for media detail should return 422."""
        response = await authed_client.get("/media/not-a-uuid/detail")
        assert response.status_code == 422


class TestSearchEndpoint:
    """Test search endpoint error handling."""

    async def test_search_empty_query_returns_200(self, authed_client: AsyncClient) -> None:
        """Empty search query should render page without error."""
        response = await authed_client.get("/search")
        assert response.status_code == 200

    async def test_search_invalid_date_shows_error(self, authed_client: AsyncClient) -> None:
        """Invalid date format should show error, not crash."""
        response = await authed_client.get("/search?q=test&since=bad-date")
        assert response.status_code == 200
        assert "Invalid start date" in response.text


class TestEntityDetail:
    """Test the generic entity detail endpoint."""

    async def test_entity_detail_unknown_type_404(self, authed_client: AsyncClient) -> None:
        """Unknown entity type should return 404."""
        fake_id = uuid4()
        response = await authed_client.get(f"/entity/nonexistent_type/{fake_id}")
        assert response.status_code == 404

    async def test_entity_detail_not_found_404(self, authed_client: AsyncClient) -> None:
        """Non-existent entity should return 404."""
        fake_id = uuid4()
        response = await authed_client.get(f"/entity/email/{fake_id}")
        assert response.status_code == 404

    async def test_entity_person_redirects(self, authed_client: AsyncClient) -> None:
        """Person entity type should redirect to /people/{id}."""
        fake_id = uuid4()
        response = await authed_client.get(f"/entity/person/{fake_id}", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == f"/people/{fake_id}"


class TestMapEndpoints:
    """Test map page and marker endpoints."""

    async def test_map_page_200(self, authed_client: AsyncClient) -> None:
        """Map page should return 200."""
        response = await authed_client.get("/map")
        assert response.status_code == 200
        assert "Map" in response.text

    async def test_map_markers_json(self, authed_client: AsyncClient) -> None:
        """Map markers endpoint should return JSON with markers array."""
        response = await authed_client.get("/map/markers")
        assert response.status_code == 200
        data = response.json()
        assert "markers" in data
        assert isinstance(data["markers"], list)

    async def test_map_markers_accepts_date_params(self, authed_client: AsyncClient) -> None:
        """Map markers should accept since/until date parameters."""
        response = await authed_client.get("/map/markers?since=2025-01-01&until=2025-12-31")
        assert response.status_code == 200
        data = response.json()
        assert "markers" in data

    async def test_map_markers_ignores_invalid_dates(self, authed_client: AsyncClient) -> None:
        """Map markers should gracefully ignore invalid date strings."""
        response = await authed_client.get("/map/markers?since=bad-date&until=also-bad")
        assert response.status_code == 200
        data = response.json()
        assert "markers" in data

    async def test_map_page_loads_markercluster_js(self, authed_client: AsyncClient) -> None:
        """Map page should include leaflet.markercluster script."""
        response = await authed_client.get("/map")
        assert response.status_code == 200
        assert "leaflet.markercluster" in response.text


class TestTimelineEndpoints:
    """Test timeline page and items endpoints."""

    async def test_timeline_page_200(self, authed_client: AsyncClient) -> None:
        """Timeline page should return 200."""
        response = await authed_client.get("/timeline")
        assert response.status_code == 200
        assert "Timeline" in response.text

    async def test_timeline_items_200(self, authed_client: AsyncClient) -> None:
        """Timeline items partial should return 200."""
        response = await authed_client.get("/timeline/items?before=2026-01-01T00:00:00")
        assert response.status_code == 200


class TestSearchBrowseMode:
    """Test search browse mode (empty query with type filter)."""

    async def test_search_browse_mode(self, authed_client: AsyncClient) -> None:
        """Browse mode should return 200 with browse results when type is set but q is empty."""
        response = await authed_client.get("/search?type=email")
        assert response.status_code == 200
        assert (
            "browse" in response.text.lower()
            or "Emails" in response.text
            or "No items" in response.text
        )


class TestTagsEndpoints:
    """Test tags CRUD endpoints."""

    async def test_tags_list_200(self, authed_client: AsyncClient) -> None:
        """Tags list should return 200."""
        response = await authed_client.get("/tags")
        assert response.status_code == 200

    async def test_edit_nonexistent_tag_404(self, authed_client: AsyncClient) -> None:
        """Editing a non-existent tag should return 404."""
        fake_id = uuid4()
        response = await authed_client.post(
            f"/tags/{fake_id}/edit",
            data={"name": "updated"},
            follow_redirects=False,
        )
        assert response.status_code == 404

    async def test_delete_nonexistent_tag_404(self, authed_client: AsyncClient) -> None:
        """Deleting a non-existent tag should return 404."""
        fake_id = uuid4()
        response = await authed_client.post(
            f"/tags/{fake_id}/delete",
            follow_redirects=False,
        )
        assert response.status_code == 404


class TestImportEndpoints:
    """Test import page endpoints."""

    async def test_imports_page_200(self, authed_client: AsyncClient) -> None:
        """Imports page should return 200."""
        response = await authed_client.get("/imports")
        assert response.status_code == 200

    async def test_imports_shows_upload_form(self, authed_client: AsyncClient) -> None:
        """Imports page should contain an upload form."""
        response = await authed_client.get("/imports")
        assert response.status_code == 200
        assert "upload" in response.text.lower() or "import" in response.text.lower()

    async def test_active_imports_partial_200(self, authed_client: AsyncClient) -> None:
        """Active imports partial should return 200."""
        response = await authed_client.get("/imports/active")
        assert response.status_code == 200

    async def test_active_imports_partial_contains_polling_attr(
        self, authed_client: AsyncClient
    ) -> None:
        """Active imports partial should contain HTMX polling attributes."""
        response = await authed_client.get("/imports/active")
        assert response.status_code == 200
        assert 'hx-get="/imports/active"' in response.text
        assert 'hx-trigger="every 3s"' in response.text


class TestMediaServing:
    """Test media file serving endpoints."""

    async def test_serve_media_not_found(self, authed_client: AsyncClient) -> None:
        """Non-existent media should return 404."""
        fake_id = uuid4()
        response = await authed_client.get(f"/media/file/{fake_id}")
        assert response.status_code == 404

    async def test_serve_thumbnail_not_found(self, authed_client: AsyncClient) -> None:
        """Non-existent thumbnail should return 404."""
        fake_id = uuid4()
        response = await authed_client.get(f"/media/thumb/{fake_id}")
        assert response.status_code == 404


class TestExtractMarkerExtras:
    """Unit tests for _extract_marker_extras helper."""

    def test_location_visit_extras(self) -> None:
        """Location visit should return place, time, duration, activity, address."""
        entity = SimpleNamespace(
            id=uuid4(),
            place_name="Central Park",
            started_at=datetime(2025, 6, 15, 14, 30, tzinfo=UTC),
            duration_minutes=45,
            activity_type="walking",
            address="New York, NY",
        )
        extras = _extract_marker_extras(entity, EntityType.LOCATION_VISIT)
        assert extras["Place"] == "Central Park"
        assert "Jun 15, 2025" in extras["Time"]
        assert extras["Duration (min)"] == "45"
        assert extras["Activity"] == "walking"
        assert extras["Address"] == "New York, NY"

    def test_location_extras(self) -> None:
        """Location should return type, address, city, country."""
        entity = SimpleNamespace(
            id=uuid4(),
            location_type="restaurant",
            address="123 Main St",
            city="Austin",
            country="US",
        )
        extras = _extract_marker_extras(entity, EntityType.LOCATION)
        assert extras["Type"] == "restaurant"
        assert extras["Address"] == "123 Main St"
        assert extras["City"] == "Austin"
        assert extras["Country"] == "US"

    def test_media_extras_includes_media_id(self) -> None:
        """Media should return media_type, date, caption, and _media_id for thumbnails."""
        mid = uuid4()
        entity = SimpleNamespace(
            id=mid,
            media_type="image",
            occurred_at=datetime(2025, 3, 10, 9, 0, tzinfo=UTC),
            caption="Sunset over the lake",
        )
        extras = _extract_marker_extras(entity, EntityType.MEDIA)
        assert extras["Media"] == "image"
        assert "Mar 10, 2025" in extras["Date"]
        assert extras["Caption"] == "Sunset over the lake"
        assert extras["_media_id"] == str(mid)

    def test_calendar_event_extras(self) -> None:
        """Calendar event should return summary, start, end, location."""
        entity = SimpleNamespace(
            id=uuid4(),
            summary="Team standup",
            start_time=datetime(2025, 7, 1, 10, 0, tzinfo=UTC),
            end_time=datetime(2025, 7, 1, 10, 30, tzinfo=UTC),
            location_text="Room 4B",
        )
        extras = _extract_marker_extras(entity, EntityType.CALENDAR_EVENT)
        assert extras["Event"] == "Team standup"
        assert "Jul 01, 2025" in extras["Start"]
        assert extras["Location"] == "Room 4B"

    def test_none_values_omitted(self) -> None:
        """None and empty string values should be omitted from extras."""
        entity = SimpleNamespace(
            id=uuid4(),
            place_name=None,
            started_at=datetime(2025, 1, 1, tzinfo=UTC),
            duration_minutes=None,
            activity_type="",
            address=None,
        )
        extras = _extract_marker_extras(entity, EntityType.LOCATION_VISIT)
        assert "Place" not in extras
        assert "Duration (min)" not in extras
        assert "Activity" not in extras
        assert "Address" not in extras
        assert "Time" in extras  # started_at is set

    def test_long_caption_truncated(self) -> None:
        """Captions longer than 80 chars should be truncated."""
        entity = SimpleNamespace(
            id=uuid4(),
            media_type="image",
            occurred_at=None,
            caption="A" * 100,
        )
        extras = _extract_marker_extras(entity, EntityType.MEDIA)
        assert len(extras["Caption"]) == 80
        assert extras["Caption"].endswith("...")
