"""API shell: health, stats parity with the service, OpenAPI, SPA mount/fallback."""

from pathlib import Path

from fastapi.testclient import TestClient

from potluck import __version__
from potluck.api.app import create_app
from potluck.core.config import Settings
from potluck.services.context import AppContext, create_context
from potluck.services.stats import get_stats


def test_health(api_client: TestClient) -> None:
    resp = api_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__}


def test_stats_matches_service_dto(api_client: TestClient, ctx: AppContext) -> None:
    resp = api_client.get("/api/stats")
    assert resp.status_code == 200
    assert resp.json() == get_stats(ctx).model_dump()


def test_openapi_and_docs_served(api_client: TestClient) -> None:
    assert api_client.get("/api/openapi.json").status_code == 200
    assert api_client.get("/api/docs").status_code == 200


def test_root_fallback_message_without_spa_build(api_client: TestClient) -> None:
    resp = api_client.get("/")
    assert resp.status_code == 200
    assert "SPA build" in resp.text
    assert "npm" in resp.text


def test_spa_served_when_dist_present(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>potluck spa</body></html>")
    context = create_context(Settings(web_dist=dist))
    try:
        with TestClient(create_app(context)) as client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert "potluck spa" in resp.text
    finally:
        context.db.close()
