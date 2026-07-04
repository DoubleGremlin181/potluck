"""REST contract tests (#131): /api/search, /api/items/{item_id} (+/thread),
the uniform error envelope, and OpenAPI accuracy.

Success shapes are asserted against the SAME service calls the endpoints
adapt (DTO parity, like test_app.py); error shapes against the envelope
``{"error": {"code", "message"[, "detail"]}}``. Corpora are real ingests via
the conftest draft factory — no mocks.
"""

from typing import Any

from fastapi.testclient import TestClient
from httpx2 import Response  # starlette 1.x TestClient is an httpx2.Client

from potluck.models.items import ItemKind
from potluck.models.search import SearchRequest
from potluck.services import items as items_service
from potluck.services import search as search_service
from potluck.services import threads as threads_service
from potluck.services.context import AppContext
from tests.conftest import email_draft, email_item_id, ingest_email_drafts

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apple_corpus(ctx: AppContext, count: int = 25) -> None:
    """Ingest *count* emails all matching "apple" (and "orchard")."""
    drafts = [
        email_draft(n, title=f"apple note {n}", text=f"orchard apple number {n}")
        for n in range(1, count + 1)
    ]
    ingest_email_drafts(ctx, *drafts)


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


# ---------------------------------------------------------------------------
# /api/search — success shapes
# ---------------------------------------------------------------------------


def test_search_matches_service_dto(api_client: TestClient, ctx: AppContext) -> None:
    _apple_corpus(ctx)

    resp = api_client.get("/api/search", params={"q": "apple", "limit": 10})

    assert resp.status_code == 200
    expected = search_service.search(ctx, SearchRequest(query="apple", limit=10))
    assert resp.json() == expected.model_dump(mode="json")
    assert len(resp.json()["hits"]) == 10
    assert resp.json()["next_cursor"] is not None
    assert resp.json()["warnings"] == []


def test_search_filter_params_match_structured_service_fields(
    api_client: TestClient, ctx: AppContext
) -> None:
    """kind/source/from_addr/after/before params map to the structured
    SearchRequest fields (which win over inline operators); from_addr is
    case-normalized by the service, not the adapter."""
    _apple_corpus(ctx)

    resp = api_client.get(
        "/api/search",
        params={
            "q": "apple",
            "kind": "email",
            "source": "gmail-test",
            "from_addr": "SENDER3@potluck.test",
            "after": "2024-01-01",
            "before": "2030-01-01",
        },
    )

    assert resp.status_code == 200
    expected = search_service.search(
        ctx,
        SearchRequest(
            query="apple",
            kinds=[ItemKind.EMAIL],
            sources=["gmail-test"],
            from_addrs=["SENDER3@potluck.test"],
            after=SearchRequest.model_validate({"query": "x", "after": "2024-01-01"}).after,
            before=SearchRequest.model_validate({"query": "x", "before": "2030-01-01"}).before,
        ),
    )
    assert resp.json() == expected.model_dump(mode="json")
    assert len(resp.json()["hits"]) == 1  # only sender3's email


def test_search_prefix_mode_matches_service(api_client: TestClient, ctx: AppContext) -> None:
    _apple_corpus(ctx, count=3)

    resp = api_client.get("/api/search", params={"q": "appl", "prefix": "true"})

    assert resp.status_code == 200
    expected = search_service.search(ctx, SearchRequest(query="appl", prefix=True))
    assert resp.json() == expected.model_dump(mode="json")
    assert len(resp.json()["hits"]) == 3


def test_search_warnings_passthrough(api_client: TestClient, ctx: AppContext) -> None:
    _apple_corpus(ctx, count=3)

    resp = api_client.get("/api/search", params={"q": "apple kind:bogus"})

    assert resp.status_code == 200
    assert resp.json()["warnings"], "typo'd inline operator must surface as a warning"
    expected = search_service.search(ctx, SearchRequest(query="apple kind:bogus"))
    assert resp.json()["warnings"] == expected.warnings
    assert len(resp.json()["hits"]) == 3  # the bad filter is dropped, not applied


def test_search_pagination_walk_no_dup_no_miss(api_client: TestClient, ctx: AppContext) -> None:
    """Keyset walk over the API: 25 hits at limit=10 -> 10/10/5, no
    duplicates, no misses, cursor exhausts to None."""
    _apple_corpus(ctx, count=25)

    pages: list[list[int]] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"q": "apple", "limit": 10}
        if cursor is not None:
            params["cursor"] = cursor
        resp = api_client.get("/api/search", params=params)
        assert resp.status_code == 200
        body = resp.json()
        pages.append([hit["id"] for hit in body["hits"]])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert [len(p) for p in pages] == [10, 10, 5]
    walked = [item_id for page in pages for item_id in page]
    assert len(set(walked)) == 25, "pagination must neither duplicate nor skip hits"
    one_shot = search_service.search(ctx, SearchRequest(query="apple", limit=100))
    assert set(walked) == {hit.id for hit in one_shot.hits}


# ---------------------------------------------------------------------------
# /api/search — error shapes
# ---------------------------------------------------------------------------


def test_search_missing_q_is_422_envelope(api_client: TestClient) -> None:
    err = _assert_envelope(api_client.get("/api/search"), 422, "validation_error")
    assert isinstance(err["detail"], list) and err["detail"]


def test_search_bad_kind_is_422_envelope(api_client: TestClient) -> None:
    resp = api_client.get("/api/search", params={"q": "apple", "kind": "bogus"})
    err = _assert_envelope(resp, 422, "validation_error")
    assert any("kind" in str(entry.get("loc", [])) for entry in err["detail"])


def test_search_oversized_list_is_422_envelope(api_client: TestClient) -> None:
    resp = api_client.get(
        "/api/search",
        params=tuple([("q", "apple")] + [("source", f"s{i}") for i in range(65)]),
    )
    _assert_envelope(resp, 422, "validation_error")


def test_search_overlong_query_is_422_envelope(api_client: TestClient) -> None:
    resp = api_client.get("/api/search", params={"q": "a" * 1001})
    _assert_envelope(resp, 422, "validation_error")


def test_search_limit_bounds_are_422_envelope(api_client: TestClient) -> None:
    _assert_envelope(
        api_client.get("/api/search", params={"q": "x", "limit": 0}), 422, "validation_error"
    )
    _assert_envelope(
        api_client.get("/api/search", params={"q": "x", "limit": 101}), 422, "validation_error"
    )


def test_search_malformed_cursor_is_400_envelope(api_client: TestClient, ctx: AppContext) -> None:
    _apple_corpus(ctx, count=3)
    resp = api_client.get("/api/search", params={"q": "apple", "cursor": "not-a-cursor"})
    _assert_envelope(resp, 400, "invalid_cursor")


def test_search_foreign_cursor_is_400_envelope(api_client: TestClient, ctx: AppContext) -> None:
    """A cursor is bound to the query that produced it: replaying it under a
    different query is a 400, never a silently wrong page."""
    _apple_corpus(ctx, count=25)
    first = api_client.get("/api/search", params={"q": "apple", "limit": 10})
    cursor = first.json()["next_cursor"]
    assert cursor is not None

    resp = api_client.get("/api/search", params={"q": "orchard", "cursor": cursor})
    _assert_envelope(resp, 400, "invalid_cursor")


# ---------------------------------------------------------------------------
# /api/items/{item_id} and /thread
# ---------------------------------------------------------------------------


def test_get_item_matches_service_and_includes_email_detail(
    api_client: TestClient, ctx: AppContext
) -> None:
    ingest_email_drafts(
        ctx,
        email_draft(
            1,
            to_addrs=("kavish@potluck.test",),
            to_names=("Kavish",),
            labels=("Inbox",),
        ),
    )
    item_id = email_item_id(ctx, "m1@potluck.test")

    resp = api_client.get(f"/api/items/{item_id}")

    assert resp.status_code == 200
    expected = items_service.get_item(ctx, item_id)
    assert resp.json() == expected.model_dump(mode="json")
    assert resp.json()["email"]["message_id"] == "m1@potluck.test"
    assert resp.json()["email"]["to_addrs"] == ["kavish@potluck.test"]


def test_get_item_missing_is_404_envelope(api_client: TestClient) -> None:
    err = _assert_envelope(api_client.get("/api/items/999999"), 404, "item_not_found")
    assert "999999" in err["message"]


def test_get_item_bad_id_is_422_envelope(api_client: TestClient) -> None:
    _assert_envelope(api_client.get("/api/items/banana"), 422, "validation_error")


def test_get_thread_matches_service(api_client: TestClient, ctx: AppContext) -> None:
    root = email_draft(1)
    reply = email_draft(2, in_reply_to="m1@potluck.test", thread_key="m1@potluck.test")
    reply2 = email_draft(3, in_reply_to="m2@potluck.test", thread_key="m1@potluck.test")
    ingest_email_drafts(ctx, root, reply, reply2)
    leaf_id = email_item_id(ctx, "m3@potluck.test")

    resp = api_client.get(f"/api/items/{leaf_id}/thread")

    assert resp.status_code == 200
    expected = threads_service.get_thread(ctx, leaf_id)
    assert resp.json() == expected.model_dump(mode="json")
    assert resp.json()["thread_key"] == "m1@potluck.test"
    assert len(resp.json()["entries"]) == 3


def test_get_thread_missing_is_404_envelope(api_client: TestClient) -> None:
    _assert_envelope(api_client.get("/api/items/999999/thread"), 404, "item_not_found")


# ---------------------------------------------------------------------------
# Envelope coverage of the pre-existing endpoints
# ---------------------------------------------------------------------------


def test_items_listing_validation_errors_use_envelope(api_client: TestClient) -> None:
    err = _assert_envelope(
        api_client.get("/api/items", params={"limit": 0}), 422, "validation_error"
    )
    assert isinstance(err["detail"], list) and err["detail"]
    _assert_envelope(
        api_client.get("/api/items", params={"sort": "bogus"}), 422, "validation_error"
    )


def test_unknown_api_path_is_enveloped_404(api_client: TestClient) -> None:
    """All /api/* error responses use the envelope — including router-level
    404s, not just service errors."""
    _assert_envelope(api_client.get("/api/nope"), 404, "not_found")


# ---------------------------------------------------------------------------
# OpenAPI accuracy (guards documentation drift)
# ---------------------------------------------------------------------------


def test_openapi_documents_search_and_items_contracts(api_client: TestClient) -> None:
    spec = api_client.get("/api/openapi.json").json()
    paths = spec["paths"]

    # --- /api/search: params, response models, both error responses -------
    search_op = paths["/api/search"]["get"]
    params = {p["name"]: p for p in search_op["parameters"]}
    assert set(params) == {
        "q",
        "kind",
        "source",
        "from_addr",
        "after",
        "before",
        "prefix",
        "cursor",
        "limit",
    }
    assert params["q"]["required"] is True
    assert all(p["in"] == "query" for p in params.values())
    assert all(p.get("description") for p in params.values()), "every param is documented"
    ok_schema = search_op["responses"]["200"]["content"]["application/json"]["schema"]
    assert ok_schema["$ref"] == "#/components/schemas/SearchResponse"
    for status in ("400", "422"):
        err_schema = search_op["responses"][status]["content"]["application/json"]["schema"]
        assert err_schema["$ref"] == "#/components/schemas/ErrorEnvelope"
    # The keyset pagination contract is spelled out on the operation.
    assert "cursor" in search_op["description"]

    # --- /api/items: offset pagination stays, documented distinctly -------
    items_op = paths["/api/items"]["get"]
    item_params = {p["name"] for p in items_op["parameters"]}
    assert {"kind", "source", "since", "until", "sort", "limit", "offset"} <= item_params
    assert (
        items_op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/ListItemsResponse"
    )
    assert (
        items_op["responses"]["422"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/ErrorEnvelope"
    )
    assert "offset" in items_op["description"]

    # --- /api/items/{item_id} and /thread: 200 DTOs + 404 envelope --------
    detail_op = paths["/api/items/{item_id}"]["get"]
    assert (
        detail_op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/Item"
    )
    thread_op = paths["/api/items/{item_id}/thread"]["get"]
    assert (
        thread_op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/ThreadResponse"
    )
    for op in (detail_op, thread_op):
        assert (
            op["responses"]["404"]["content"]["application/json"]["schema"]["$ref"]
            == "#/components/schemas/ErrorEnvelope"
        )

    # --- envelope schema itself -------------------------------------------
    envelope = spec["components"]["schemas"]["ErrorEnvelope"]
    assert set(envelope["properties"]) == {"error"}
    error_detail = spec["components"]["schemas"]["ErrorDetail"]
    assert {"code", "message"} <= set(error_detail["properties"])

    # Every operation carries a human summary.
    for path_item in paths.values():
        for op in path_item.values():
            assert op.get("summary"), f"operation without summary: {op.get('operationId')}"
