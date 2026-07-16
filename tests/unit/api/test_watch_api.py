"""GET/PATCH /api/watch (#151): envelope, persistence, race-safety."""

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from potluck.api.app import create_app
from potluck.services import watch as watch_service
from potluck.services.context import AppContext, create_context


def test_get_watch_default_shape(api_client: TestClient) -> None:
    resp = api_client.get("/api/watch")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["effective_enabled_source"] == "config"
    assert body["interval_s"] == 10.0  # shipped default: 2 intervals = 20 s react < 30 s (I1)
    assert body["folders"] == []
    assert body["last_scan_at"] is None
    assert body["pending"] == []
    assert body["last_error"] is None


def test_patch_toggles_and_reports_runtime_source(api_client: TestClient) -> None:
    resp = api_client.patch("/api/watch", json={"enabled": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["effective_enabled_source"] == "runtime"

    # GET reflects the persisted override.
    got = api_client.get("/api/watch").json()
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
        assert client.patch("/api/watch", json={"enabled": False}).status_code == 200

    rebuilt = create_context(ctx.settings)
    try:
        status = watch_service.get_watch_status(rebuilt)
        assert status.enabled is False
        assert status.effective_enabled_source == "runtime"
    finally:
        rebuilt.db.close()


def test_patch_validation_error_envelope(api_client: TestClient) -> None:
    resp = api_client.patch("/api/watch", json={"enabled": "definitely"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["detail"]


def test_patch_missing_body_enveloped(api_client: TestClient) -> None:
    resp = api_client.patch("/api/watch", json={})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


@pytest.fixture
def watching_client(ctx: AppContext, tmp_path: Path) -> Iterator[tuple[TestClient, AppContext]]:
    """TestClient over a context whose watcher thread is live (folders
    configured, 0.02 s interval) — the serve lifespan starts/stops it."""
    folder = tmp_path / "watched"
    folder.mkdir()
    wctx = AppContext(
        settings=ctx.settings.model_copy(
            update={
                "web_dist": tmp_path / "no-spa",
                "watch_folders": [folder],
                "watch_interval_s": 0.02,
            }
        ),
        db=ctx.db,
    )
    with TestClient(create_app(wctx)) as client:
        yield client, wctx


def test_lifespan_starts_and_stops_the_watcher(ctx: AppContext, tmp_path: Path) -> None:
    folder = tmp_path / "watched"
    folder.mkdir()
    wctx = AppContext(
        settings=ctx.settings.model_copy(
            update={
                "web_dist": tmp_path / "no-spa",
                "watch_folders": [folder],
                "watch_interval_s": 0.02,
            }
        ),
        db=ctx.db,
    )
    with TestClient(create_app(wctx)) as client:
        deadline = time.monotonic() + 30.0
        while client.get("/api/watch").json()["last_scan_at"] is None:
            assert time.monotonic() < deadline, "lifespan watcher never scanned"
            time.sleep(0.01)
        body = client.get("/api/watch").json()
        assert [f["path"] for f in body["folders"]] == [str(folder)]
        assert body["folders"][0]["exists"] is True

    # Lifespan shutdown stopped the polling thread.
    wctx.watcher.join(5.0)
    assert not wctx.watcher.is_running()


def test_patch_while_watcher_mid_cycle_is_race_safe(
    watching_client: tuple[TestClient, AppContext],
) -> None:
    """Hammer the toggle while cycles run: every PATCH succeeds (the KV write
    goes through the single writer thread; the watcher only reads), and the
    final state is exactly the last write."""
    client, _ = watching_client
    for i in range(20):
        want = i % 2 == 0
        resp = client.patch("/api/watch", json={"enabled": want})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is want
    final = client.get("/api/watch").json()
    assert final["enabled"] is False  # last write: i=19 -> want False
    assert final["effective_enabled_source"] == "runtime"


def test_watch_endpoints_in_openapi(api_client: TestClient) -> None:
    spec = api_client.get("/api/openapi.json").json()
    assert "get" in spec["paths"]["/api/watch"]
    assert "patch" in spec["paths"]["/api/watch"]
