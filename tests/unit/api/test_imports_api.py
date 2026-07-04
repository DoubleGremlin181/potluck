"""REST contract tests (#132): POST /api/imports (path + upload), the
/api/imports/status poll handle, history, /api/imports/{id}, /api/sources.

Success shapes are asserted against the SAME service calls the endpoints
adapt (DTO parity); error shapes against the uniform envelope. Corpora are
real generator-built archives — no mocks, no blind sleeps (all waits poll a
condition against a deadline).
"""

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from httpx2 import Response  # starlette 1.x TestClient is an httpx2.Client

from potluck.api.app import create_app
from potluck.ingest.plugins import Glob, ParseContext, source
from potluck.ingest.readers import Archive
from potluck.models.drafts import NoteDraft
from potluck.models.items import ItemKind
from potluck.services import imports as imports_service
from potluck.services.context import AppContext
from potluck.testing.archives import write_archive
from potluck.testing.keep import write_keep_takeout
from tests.conftest import insert_import, insert_source

_DEADLINE_S = 30.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_envelope(resp: Response, status: int, code: str) -> dict[str, Any]:
    """Assert *resp* is the uniform error envelope; return the error object."""
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}, f"non-envelope error body: {body}"
    err = body["error"]
    assert isinstance(err, dict)
    assert err["code"] == code
    assert isinstance(err["message"], str) and err["message"]
    return dict(err)


def _wait_for(predicate: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + _DEADLINE_S
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def _poll_terminal(client: TestClient) -> dict[str, Any]:
    """Poll GET /api/imports/status until the task leaves 'running'."""

    def _done() -> bool:
        body = client.get("/api/imports/status").json()
        return body is not None and body["status"] != "running"

    _wait_for(_done, "background import to finish")
    result = client.get("/api/imports/status").json()
    assert isinstance(result, dict)
    return result


def _make_client(
    ctx: AppContext, tmp_path: Path, **settings_updates: Any
) -> tuple[TestClient, AppContext]:
    """A no-SPA TestClient over ctx's database (like the api_client fixture),
    for tests that must act BEFORE lifespan startup or inspect the app's own
    AppContext (its import manager) afterwards."""
    app_ctx = AppContext(
        settings=ctx.settings.model_copy(
            update={"web_dist": tmp_path / "no-spa"} | settings_updates
        ),
        db=ctx.db,
    )
    return TestClient(create_app(app_ctx)), app_ctx


# ---------------------------------------------------------------------------
# POST /api/imports (server path) -> 202 + poll flow
# ---------------------------------------------------------------------------


def test_post_import_202_then_poll_to_completed(
    api_client: TestClient, ctx: AppContext, tmp_path: Path
) -> None:
    archive = write_keep_takeout(tmp_path / "keep", 5, seed=7)

    resp = api_client.post("/api/imports", json={"path": str(archive)})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "running"
    assert body["path"] == str(archive)
    assert body["import_ids"] == []
    assert body["error"] is None

    status = _poll_terminal(api_client)
    assert status["status"] == "completed", status
    [import_id] = status["import_ids"]

    single = api_client.get(f"/api/imports/{import_id}")
    assert single.status_code == 200
    run = single.json()
    assert run["status"] == "completed"
    assert run["items_new"] > 0
    assert (
        run["items_done"]
        == run["items_new"] + run["items_duplicate"] + run["items_updated"] + run["items_skipped"]
    )
    assert run["items_total"] is None
    # DTO parity with the service the endpoint adapts.
    assert run == imports_service.get_import(ctx, import_id).model_dump(mode="json")


def test_post_import_missing_path_is_400_envelope(api_client: TestClient) -> None:
    resp = api_client.post("/api/imports", json={"path": "/nope/missing.zip"})
    err = _assert_envelope(resp, 400, "unsupported_archive")
    assert "missing.zip" in err["message"]


def test_post_import_missing_body_field_is_422_envelope(api_client: TestClient) -> None:
    _assert_envelope(api_client.post("/api/imports", json={}), 422, "validation_error")


# ---------------------------------------------------------------------------
# Conflict: 409 envelope while an import is running
# ---------------------------------------------------------------------------


def test_post_import_conflict_is_409_envelope(
    api_client: TestClient, tmp_path: Path, clean_registry: dict[str, Any]
) -> None:
    release = threading.Event()

    def parse(archive: Archive, pctx: ParseContext) -> Iterator[NoteDraft]:
        yield NoteDraft(title="first", text="gated api body")
        release.wait(timeout=_DEADLINE_S)

    source(name="gated_api", detect=Glob("*Gated/*.txt"), kinds=(ItemKind.NOTE,))(parse)
    archive = write_archive(tmp_path / "gated.zip", {"Takeout/Gated/x.txt": b"x"}, fmt="zip")

    try:
        first = api_client.post("/api/imports", json={"path": str(archive)})
        assert first.status_code == 202

        second = api_client.post("/api/imports", json={"path": str(archive)})
        _assert_envelope(second, 409, "import_in_progress")
    finally:
        release.set()

    assert _poll_terminal(api_client)["status"] == "completed"


# ---------------------------------------------------------------------------
# POST /api/imports/upload (multipart) -> stored in the managed dir, imported
# ---------------------------------------------------------------------------


def test_upload_import_202_then_completed(
    api_client: TestClient, ctx: AppContext, tmp_path: Path
) -> None:
    archive = write_keep_takeout(tmp_path / "keep", 3, seed=11)
    payload = archive.read_bytes()

    resp = api_client.post(
        "/api/imports/upload",
        files={"file": ("takeout.zip", payload, "application/zip")},
    )

    assert resp.status_code == 202, resp.text
    stored = Path(resp.json()["path"])
    assert stored.name == "takeout.zip"
    assert stored.is_relative_to(ctx.settings.uploads_dir)
    assert stored.read_bytes() == payload

    status = _poll_terminal(api_client)
    assert status["status"] == "completed", status
    [import_id] = status["import_ids"]
    run = imports_service.get_import(ctx, import_id)
    assert run.items_new > 0
    assert run.path == str(stored)


def test_upload_traversal_filename_is_confined(api_client: TestClient, ctx: AppContext) -> None:
    resp = api_client.post(
        "/api/imports/upload",
        files={"file": ("../../escape.zip", b"not really a zip", "application/zip")},
    )
    assert resp.status_code == 202, resp.text
    stored = Path(resp.json()["path"])
    assert stored.name == "escape.zip"
    assert stored.is_relative_to(ctx.settings.uploads_dir)
    # The bogus payload fails in the background; the task carries the error.
    status = _poll_terminal(api_client)
    assert status["status"] == "failed"


# ---------------------------------------------------------------------------
# GET /api/imports (history) + /api/imports/{id}
# ---------------------------------------------------------------------------


def test_history_lists_newest_first_with_limit_offset(
    api_client: TestClient, ctx: AppContext, tmp_path: Path
) -> None:
    for n, seed in ((2, 1), (3, 2)):
        imports_service.import_path(ctx, write_keep_takeout(tmp_path / f"k{seed}", n, seed=seed))

    resp = api_client.get("/api/imports")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["runs"]) == 2
    assert body["runs"][0]["id"] > body["runs"][1]["id"]  # newest first
    assert body == imports_service.list_imports(ctx).model_dump(mode="json")

    page = api_client.get("/api/imports", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 2
    assert [r["id"] for r in page["runs"]] == [body["runs"][1]["id"]]


def test_get_import_unknown_id_is_404_envelope(api_client: TestClient) -> None:
    err = _assert_envelope(api_client.get("/api/imports/999999"), 404, "import_not_found")
    assert "999999" in err["message"]


def test_get_import_bad_id_is_422_envelope(api_client: TestClient) -> None:
    _assert_envelope(api_client.get("/api/imports/banana"), 422, "validation_error")


def test_import_status_is_null_before_any_import(api_client: TestClient) -> None:
    resp = api_client.get("/api/imports/status")
    assert resp.status_code == 200
    assert resp.json() is None


# ---------------------------------------------------------------------------
# GET /api/sources
# ---------------------------------------------------------------------------


def test_sources_lists_registered_plugins(api_client: TestClient, ctx: AppContext) -> None:
    resp = api_client.get("/api/sources")
    assert resp.status_code == 200
    body = resp.json()
    assert body == [s.model_dump(mode="json") for s in imports_service.list_sources(ctx)]
    by_name = {entry["name"]: entry for entry in body}
    assert "email" in by_name["gmail"]["kinds"]
    assert "note" in by_name["google_keep"]["kinds"]


# ---------------------------------------------------------------------------
# OpenAPI documents the new surface
# ---------------------------------------------------------------------------


def test_openapi_documents_imports_endpoints(api_client: TestClient) -> None:
    spec = api_client.get("/api/openapi.json").json()
    paths = spec["paths"]
    assert "post" in paths["/api/imports"]
    assert paths["/api/imports"]["post"]["responses"].keys() >= {"202", "400", "409", "422"}
    assert "get" in paths["/api/imports"]
    assert "post" in paths["/api/imports/upload"]
    assert "413" in paths["/api/imports/upload"]["post"]["responses"]
    assert "get" in paths["/api/imports/status"]
    assert "get" in paths["/api/imports/{import_id}"]
    assert "404" in paths["/api/imports/{import_id}"]["get"]["responses"]
    assert "get" in paths["/api/sources"]


# ---------------------------------------------------------------------------
# Write-ownership recovery (#132 review): serve startup sweeps, shutdown joins
# ---------------------------------------------------------------------------


def test_serve_startup_sweeps_stale_running_rows(ctx: AppContext, tmp_path: Path) -> None:
    """A restarted SERVER recovers stale 'running' rows during lifespan
    startup — before the first request can observe phantom progress."""
    iid = ctx.db.write(lambda conn: insert_import(conn, insert_source(conn)))

    client, _ = _make_client(ctx, tmp_path)
    with client:
        run = client.get(f"/api/imports/{iid}").json()

    assert run["status"] == "failed"
    assert run["error"] == "interrupted"
    assert run["finished_at"] is not None


def test_shutdown_joins_finishing_import(
    ctx: AppContext, tmp_path: Path, clean_registry: dict[str, Any]
) -> None:
    """Clean shutdown gives the worker a bounded join, so ordinary exits
    settle the task/row instead of leaning on next-open recovery."""
    release = threading.Event()

    def parse(archive: Archive, pctx: ParseContext) -> Iterator[NoteDraft]:
        yield NoteDraft(title="a", text="join body")
        release.wait(timeout=_DEADLINE_S)

    source(name="join_src", detect=Glob("*Gated/*.txt"), kinds=(ItemKind.NOTE,))(parse)
    archive = write_archive(tmp_path / "gated.zip", {"Takeout/Gated/x.txt": b"x"}, fmt="zip")

    client, app_ctx = _make_client(ctx, tmp_path)
    with client:
        assert client.post("/api/imports", json={"path": str(archive)}).status_code == 202
        release.set()

    # Lifespan shutdown joined the worker: terminal in-memory task AND a
    # terminal ledger row, no recovery needed on the next open.
    task = app_ctx.import_manager.status()
    assert task is not None and task.status == "completed"
    [import_id] = task.import_ids
    assert imports_service.get_import(ctx, import_id).status == "completed"


# ---------------------------------------------------------------------------
# Upload size cap -> 413 envelope
# ---------------------------------------------------------------------------


def test_upload_over_limit_is_413_envelope(ctx: AppContext, tmp_path: Path) -> None:
    client, _ = _make_client(ctx, tmp_path, max_upload_bytes=256)
    with client:
        resp = client.post(
            "/api/imports/upload",
            files={"file": ("big.zip", b"x" * 1024, "application/zip")},
        )
        err = _assert_envelope(resp, 413, "upload_too_large")
        assert "max_upload_bytes" in err["message"]
        # Nothing may be left in the managed store.
        uploads = ctx.settings.uploads_dir
        leftovers = list(uploads.rglob("*")) if uploads.exists() else []
        assert leftovers == []
