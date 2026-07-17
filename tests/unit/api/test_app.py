"""API shell: health, stats/items parity with the service, OpenAPI, SPA mount/fallback."""

import webbrowser
from pathlib import Path

import pytest
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


def test_root_fallback_page_without_spa_build(api_client: TestClient) -> None:
    """Source installs (uvx --from git+…) have no web build (#141): "/" must
    serve an HTML page that says the server works and routes the user to the
    API docs, the MCP endpoint, and the release install that bundles the SPA."""
    resp = api_client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "/api/docs" in resp.text
    assert "/mcp" in resp.text
    assert "https://github.com/DoubleGremlin181/potluck/releases" in resp.text
    assert "npm run" in resp.text  # checkout users still get the build hint


def _spa_client(tmp_path: Path) -> tuple[TestClient, AppContext]:
    """create_app over a minimal fake SPA build (index.html + one asset)."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>potluck spa</body></html>")
    (dist / "assets" / "app-test.js").write_text("console.log('potluck asset')")
    context = create_context(Settings(web_dist=dist))
    return TestClient(create_app(context)), context


def test_spa_served_when_dist_present(tmp_path: Path) -> None:
    client, context = _spa_client(tmp_path)
    try:
        with client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert "potluck spa" in resp.text
    finally:
        context.db.close()


def test_spa_fallback_serves_index_for_client_routes(tmp_path: Path) -> None:
    """Hard reloads of client routes (deep links) get index.html with a 200 —
    the SPA router takes over from there (#135 carried-over fix A)."""
    client, context = _spa_client(tmp_path)
    try:
        with client:
            for path in ("/items/5", "/settings", "/imports", "/items/5/nested"):
                resp = client.get(path)
                assert resp.status_code == 200, path
                assert "potluck spa" in resp.text, path
                assert resp.headers["content-type"].startswith("text/html"), path
    finally:
        context.db.close()


def test_spa_fallback_leaves_real_assets_and_dotted_paths_alone(tmp_path: Path) -> None:
    """Real static assets still stream from disk; an unknown dotted file
    (e.g. a missing favicon) stays a plain 404 — never index.html."""
    client, context = _spa_client(tmp_path)
    try:
        with client:
            asset = client.get("/assets/app-test.js")
            assert asset.status_code == 200
            assert "potluck asset" in asset.text

            missing = client.get("/favicon-missing.png")
            assert missing.status_code == 404
            assert "potluck spa" not in missing.text

            missing_asset = client.get("/assets/gone.js")
            assert missing_asset.status_code == 404
    finally:
        context.db.close()


def test_spa_fallback_never_swallows_api_or_mcp(tmp_path: Path) -> None:
    """/api/* keeps the enveloped 404 and /mcp keeps its 307 redirect even
    with the SPA catch-all (and its fallback) mounted."""
    client, context = _spa_client(tmp_path)
    try:
        with client:
            api_404 = client.get("/api/nope")
            assert api_404.status_code == 404
            assert api_404.json()["error"]["code"] == "not_found"

            mcp = client.get("/mcp", follow_redirects=False)
            assert mcp.status_code == 307
            assert mcp.headers["location"] == "/mcp/"

            # An extension-less path merely PREFIXED by mcp is still not SPA
            # territory (the fallback is scoped away from /mcp*, per #135).
            assert client.get("/mcp-extra").status_code == 404
    finally:
        context.db.close()


def test_lifespan_startup_failure_stops_watcher_thread(
    ctx: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lifespan enter that raises AFTER the watcher thread starts must stop
    it on the way out — the poller must not outlive a failed startup
    (task-10 review M1)."""
    folder = tmp_path / "watched"
    folder.mkdir()
    wctx = AppContext(
        settings=ctx.settings.model_copy(
            update={"watch_folders": [folder], "web_dist": tmp_path / "no-spa"}
        ),
        db=ctx.db,
    )

    def _fail(url: str) -> bool:
        raise RuntimeError("scripted startup failure")

    monkeypatch.setattr(webbrowser, "open", _fail)
    with (
        pytest.raises(RuntimeError, match="scripted startup failure"),
        TestClient(create_app(wctx, open_browser=True)),
    ):
        pass  # pragma: no cover — startup never completes
    assert not wctx.watcher.is_running()
