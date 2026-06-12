"""API shell: health, stats/items parity with the service, OpenAPI, SPA mount/fallback."""

from pathlib import Path

from fastapi.testclient import TestClient

from potluck import __version__
from potluck.api.app import create_app
from potluck.core.config import Settings
from potluck.models.items import ItemKind, ListItemsRequest
from potluck.services.context import AppContext, create_context
from potluck.services.items import list_items
from potluck.services.stats import get_stats
from tests.conftest import ingest_keep_corpus


def test_health(api_client: TestClient) -> None:
    resp = api_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__}


def test_stats_matches_service_dto(api_client: TestClient, ctx: AppContext) -> None:
    resp = api_client.get("/api/stats")
    assert resp.status_code == 200
    assert resp.json() == get_stats(ctx).model_dump()


def test_list_items_matches_service_dto(
    api_client: TestClient, ctx: AppContext, tmp_path: Path
) -> None:
    ingest_keep_corpus(ctx, tmp_path)

    resp = api_client.get("/api/items", params={"kind": "note", "limit": 5})

    assert resp.status_code == 200
    expected = list_items(ctx, ListItemsRequest(kinds=[ItemKind.NOTE], limit=5))
    assert resp.json() == expected.model_dump(mode="json")
    assert len(resp.json()["items"]) == 5
    assert resp.json()["total"] > 5


def test_list_items_date_range_and_sort(
    api_client: TestClient, ctx: AppContext, tmp_path: Path
) -> None:
    ingest_keep_corpus(ctx, tmp_path)

    resp = api_client.get(
        "/api/items",
        params={"since": "2020-01-01T00:00:00Z", "sort": "ts_asc", "limit": 100},
    )

    assert resp.status_code == 200
    ts_values = [i["ts"] for i in resp.json()["items"] if i["ts"] is not None]
    assert ts_values == sorted(ts_values)


def test_list_items_validation_errors_are_422(api_client: TestClient) -> None:
    assert api_client.get("/api/items", params={"limit": 0}).status_code == 422
    assert api_client.get("/api/items", params={"limit": 101}).status_code == 422
    assert api_client.get("/api/items", params={"sort": "bogus"}).status_code == 422
    assert api_client.get("/api/items", params={"kind": "bogus"}).status_code == 422


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
