"""GET/PATCH /api/gdrive (#152): shape, toggle persistence, validation."""

from pathlib import Path

from fastapi.testclient import TestClient

from potluck.api.app import create_app
from potluck.services import gdrive as gdrive_service
from potluck.services.context import AppContext, create_context


def test_get_gdrive_default_shape(api_client: TestClient) -> None:
    resp = api_client.get("/api/gdrive")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["auth_state"] == "unconfigured"
    assert body["enabled"] is True
    assert body["effective_enabled_source"] == "config"
    assert body["prune"] is False
    assert body["prune_scope_granted"] is False
    assert body["folder_name"] == "Takeout"
    assert body["interval_s"] == 86400.0
    assert body["pulled_files"] == 0
    assert body["last_check_at"] is None
    assert body["last_pull_at"] is None
    assert body["offline"] is False
    assert body["backoff_cycles"] is None
    assert body["last_error"] is None


def test_patch_toggles_and_reports_runtime_source(api_client: TestClient) -> None:
    resp = api_client.patch("/api/gdrive", json={"enabled": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["effective_enabled_source"] == "runtime"

    got = api_client.get("/api/gdrive").json()
    assert got["enabled"] is False
    assert got["effective_enabled_source"] == "runtime"


def test_patch_persists_across_context_rebuild(ctx: AppContext, tmp_path: Path) -> None:
    """The runtime toggle is durable: a brand-new context over the same
    database file (a server restart) still sees it."""
    no_spa = AppContext(
        settings=ctx.settings.model_copy(update={"web_dist": tmp_path / "no-spa"}),
        db=ctx.db,
    )
    with TestClient(create_app(no_spa)) as client:
        assert client.patch("/api/gdrive", json={"enabled": False}).status_code == 200

    rebuilt = create_context(ctx.settings)
    try:
        status = gdrive_service.get_gdrive_status(rebuilt)
        assert status.enabled is False
        assert status.effective_enabled_source == "runtime"
    finally:
        rebuilt.db.close()


def test_patch_validation_error_envelope(api_client: TestClient) -> None:
    resp = api_client.patch("/api/gdrive", json={"enabled": "sideways"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_gdrive_route_registered_in_openapi(api_client: TestClient) -> None:
    openapi = api_client.get("/api/openapi.json").json()
    assert set(openapi["paths"]["/api/gdrive"]) == {"get", "patch"}
