"""REST contract tests (#153): DELETE /api/imports/{id} and /api/items/{id}.

Success shapes assert the RemoveResult DTO; error shapes assert the uniform
envelope (404 unknown ids, 409 running imports). Corpora are draft-fed through
the real engine — no mocks.
"""

import sqlite3
from typing import Any

from fastapi.testclient import TestClient
from httpx2 import Response  # starlette 1.x TestClient is an httpx2.Client

from potluck.services.context import AppContext
from tests.conftest import (
    email_draft,
    ingest_email_drafts,
    insert_import,
    insert_item,
    insert_source,
)


def _assert_envelope(resp: Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}, f"non-envelope error body: {body}"
    err = body["error"]
    assert err["code"] == code
    assert isinstance(err["message"], str) and err["message"]
    return dict(err)


def _count(ctx: AppContext, sql: str) -> int:
    with ctx.db.read() as conn:
        return int(conn.execute(sql).fetchone()[0])


def _seed_running_import(ctx: AppContext) -> int:
    def _seed(conn: sqlite3.Connection) -> int:
        return insert_import(conn, insert_source(conn))  # status defaults to 'running'

    return ctx.db.write(_seed)


# ---------------------------------------------------------------------------
# DELETE /api/imports/{id}
# ---------------------------------------------------------------------------


def test_delete_import_returns_counts_then_404(ctx: AppContext, api_client: TestClient) -> None:
    import_id = ingest_email_drafts(ctx, email_draft(1), email_draft(2))

    resp = api_client.delete(f"/api/imports/{import_id}")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items_deleted": 2, "imports_deleted": 1, "hashes_suppressed": 0}
    _assert_envelope(api_client.get(f"/api/imports/{import_id}"), 404, "import_not_found")
    assert _count(ctx, "SELECT COUNT(*) FROM items") == 0


def test_delete_import_forget_suppresses(ctx: AppContext, api_client: TestClient) -> None:
    import_id = ingest_email_drafts(ctx, email_draft(1), email_draft(2))

    resp = api_client.delete(f"/api/imports/{import_id}", params={"forget": "true"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["hashes_suppressed"] == 2
    assert _count(ctx, "SELECT COUNT(*) FROM suppressed_hashes") == 2


def test_delete_import_unknown_404(api_client: TestClient) -> None:
    _assert_envelope(api_client.delete("/api/imports/999"), 404, "import_not_found")


def test_delete_import_running_409(ctx: AppContext, api_client: TestClient) -> None:
    import_id = _seed_running_import(ctx)
    _assert_envelope(api_client.delete(f"/api/imports/{import_id}"), 409, "import_running")
    assert _count(ctx, "SELECT COUNT(*) FROM imports") == 1


# ---------------------------------------------------------------------------
# DELETE /api/items/{id}
# ---------------------------------------------------------------------------


def test_delete_item_returns_counts_then_404(ctx: AppContext, api_client: TestClient) -> None:
    ingest_email_drafts(ctx, email_draft(1), email_draft(2))
    with ctx.db.read() as conn:
        item_id = int(conn.execute("SELECT MIN(id) FROM items").fetchone()[0])

    resp = api_client.delete(f"/api/items/{item_id}")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items_deleted": 1, "imports_deleted": 0, "hashes_suppressed": 0}
    _assert_envelope(api_client.get(f"/api/items/{item_id}"), 404, "item_not_found")
    assert _count(ctx, "SELECT COUNT(*) FROM items") == 1


def test_delete_item_forget_suppresses(ctx: AppContext, api_client: TestClient) -> None:
    ingest_email_drafts(ctx, email_draft(1))
    with ctx.db.read() as conn:
        item_id = int(conn.execute("SELECT MIN(id) FROM items").fetchone()[0])

    resp = api_client.delete(f"/api/items/{item_id}", params={"forget": "true"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["hashes_suppressed"] == 1
    assert _count(ctx, "SELECT COUNT(*) FROM suppressed_hashes") == 1


def test_delete_item_unknown_404(api_client: TestClient) -> None:
    _assert_envelope(api_client.delete("/api/items/424242"), 404, "item_not_found")


def test_delete_item_running_owner_409(ctx: AppContext, api_client: TestClient) -> None:
    """DELETE /api/items/{id} while the item's owning import is still running
    → the same 409 envelope the import route serves (task-11 review M2)."""

    def _seed(conn: sqlite3.Connection) -> int:
        source_id = insert_source(conn)
        import_id = insert_import(conn, source_id)  # status defaults to 'running'
        return insert_item(conn, source_id, import_id, content_hash="h1")

    item_id = ctx.db.write(_seed)
    _assert_envelope(api_client.delete(f"/api/items/{item_id}"), 409, "import_running")
    assert _count(ctx, "SELECT COUNT(*) FROM items") == 1
