"""Query language (#127): from:/source:/kind:/before:/after: parsed from the query."""

from datetime import UTC, datetime

from potluck.models.items import ItemKind
from potluck.search.query import parse_query

# ---------------------------------------------------------------------------
# parse_query
# ---------------------------------------------------------------------------


def test_plain_text_passes_through() -> None:
    parsed = parse_query("garden plans")
    assert parsed.terms == "garden plans"
    assert parsed.kinds == ()
    assert parsed.errors == ()


def test_operators_extracted_from_terms() -> None:
    parsed = parse_query("from:alice@potluck.test kind:email garden after:2024-01-01")
    assert parsed.terms == "garden"
    assert parsed.from_addrs == ("alice@potluck.test",)
    assert parsed.kinds == (ItemKind.EMAIL,)
    assert parsed.after == datetime(2024, 1, 1, tzinfo=UTC)


def test_quoted_value() -> None:
    parsed = parse_query('source:"google keep" basil')
    assert parsed.sources == ("google keep",)
    assert parsed.terms == "basil"


def test_before_and_after() -> None:
    parsed = parse_query("before:2025-06-30 after:2024-01-15")
    assert parsed.before == datetime(2025, 6, 30, tzinfo=UTC)
    assert parsed.after == datetime(2024, 1, 15, tzinfo=UTC)
    assert parsed.terms == ""


def test_repeated_operator_collects_values() -> None:
    parsed = parse_query("kind:email kind:note from:a@potluck.test from:b@potluck.test")
    assert parsed.kinds == (ItemKind.EMAIL, ItemKind.NOTE)
    assert parsed.from_addrs == ("a@potluck.test", "b@potluck.test")


def test_keys_are_case_insensitive() -> None:
    parsed = parse_query("From:alice@potluck.test KIND:email")
    assert parsed.from_addrs == ("alice@potluck.test",)
    assert parsed.kinds == (ItemKind.EMAIL,)


def test_unknown_key_stays_in_terms() -> None:
    parsed = parse_query("subject:hello world")
    assert "subject" in parsed.terms and "hello" in parsed.terms and "world" in parsed.terms
    assert parsed.errors == ()


def test_invalid_kind_recorded_as_error_not_searched() -> None:
    parsed = parse_query("kind:banana fennel")
    assert parsed.kinds == ()
    assert parsed.terms == "fennel"
    assert parsed.errors and "banana" in parsed.errors[0]


def test_invalid_date_recorded_as_error() -> None:
    parsed = parse_query("before:notadate fennel")
    assert parsed.before is None
    assert parsed.terms == "fennel"
    assert parsed.errors


def test_never_raises_on_garbage() -> None:
    for nasty in (
        "",
        ":",
        "from:",
        'from:"unterminated',
        "kind:" + "x" * 500,
        "before:9999-99-99",
        "a:b:c:d e:f",
        '"":""',
        "from:from:from:",
    ):
        parse_query(nasty)  # must not raise


# ---------------------------------------------------------------------------
# service integration: filters compose with MATCH
# ---------------------------------------------------------------------------


def _ingest_mixed(ctx: object) -> None:
    from potluck.ingest.engine import run_import
    from potluck.models.drafts import EmailDraft, ItemDraft, NoteDraft
    from potluck.services.context import AppContext

    assert isinstance(ctx, AppContext)
    run_import(
        ctx.db,
        source_name="keep-test",
        parser_version=1,
        drafts=iter([NoteDraft(title="garden notes", text="basil and fennel layout")]),
        path="/tmp/t.zip",
        file_hash=None,
    )
    drafts: list[ItemDraft] = [
        EmailDraft(
            external_id="mid:e1@potluck.test",
            message_id="e1@potluck.test",
            thread_key="e1@potluck.test",
            from_addr="alice@potluck.test",
            title="garden budget",
            text="fennel seeds invoice",
            ts=datetime(2024, 3, 1, tzinfo=UTC),
        ),
        EmailDraft(
            external_id="mid:e2@potluck.test",
            message_id="e2@potluck.test",
            thread_key="e2@potluck.test",
            from_addr="bob@potluck.test",
            title="fennel recipe",
            text="roasted fennel with lemon",
            ts=datetime(2025, 3, 1, tzinfo=UTC),
        ),
    ]
    run_import(
        ctx.db,
        source_name="gmail-test",
        parser_version=1,
        drafts=iter(drafts),
        path="/tmp/t.mbox",
        file_hash=None,
    )


def test_from_filter_composes_with_match(ctx: object) -> None:
    from potluck.models.search import SearchRequest
    from potluck.services.context import AppContext
    from potluck.services.search import search

    assert isinstance(ctx, AppContext)
    _ingest_mixed(ctx)
    resp = search(ctx, SearchRequest(query="from:alice@potluck.test fennel"))
    assert [h.title for h in resp.hits] == ["garden budget"]


def test_from_prefix_match_without_at(ctx: object) -> None:
    from potluck.models.search import SearchRequest
    from potluck.services.context import AppContext
    from potluck.services.search import search

    assert isinstance(ctx, AppContext)
    _ingest_mixed(ctx)
    resp = search(ctx, SearchRequest(query="from:bob fennel"))
    assert [h.title for h in resp.hits] == ["fennel recipe"]


def test_kind_and_date_operators(ctx: object) -> None:
    from potluck.models.search import SearchRequest
    from potluck.services.context import AppContext
    from potluck.services.search import search

    assert isinstance(ctx, AppContext)
    _ingest_mixed(ctx)
    resp = search(ctx, SearchRequest(query="kind:email after:2025-01-01 fennel"))
    assert [h.title for h in resp.hits] == ["fennel recipe"]
    resp = search(ctx, SearchRequest(query="kind:email before:2025-01-01 fennel"))
    assert [h.title for h in resp.hits] == ["garden budget"]


def test_source_operator(ctx: object) -> None:
    from potluck.models.search import SearchRequest
    from potluck.services.context import AppContext
    from potluck.services.search import search

    assert isinstance(ctx, AppContext)
    _ingest_mixed(ctx)
    resp = search(ctx, SearchRequest(query="source:gmail-test fennel"))
    assert len(resp.hits) == 2  # the note lives in keep-test
    resp = search(ctx, SearchRequest(query="source:nonexistent fennel"))
    assert resp.hits == []


def test_structured_args_win_over_inline_operators(ctx: object) -> None:
    from potluck.models.items import ItemKind
    from potluck.models.search import SearchRequest
    from potluck.services.context import AppContext
    from potluck.services.search import search

    assert isinstance(ctx, AppContext)
    _ingest_mixed(ctx)
    resp = search(ctx, SearchRequest(query="kind:email fennel", kinds=[ItemKind.NOTE]))
    assert [h.title for h in resp.hits] == ["garden notes"]


def test_filter_only_query_returns_filtered_items(ctx: object) -> None:
    """No free-text terms: filters alone return matching items, newest first."""
    from potluck.models.search import SearchRequest
    from potluck.services.context import AppContext
    from potluck.services.search import search

    assert isinstance(ctx, AppContext)
    _ingest_mixed(ctx)
    resp = search(ctx, SearchRequest(query="from:alice@potluck.test"))
    assert [h.title for h in resp.hits] == ["garden budget"]


# ---------------------------------------------------------------------------
# EXPLAIN QUERY PLAN: MATCH drives the scan; satellite access stays indexed
# ---------------------------------------------------------------------------


def test_query_plan_fts_drives_and_emails_uses_pk(ctx: object) -> None:
    from potluck.search.fts import build_search_sql
    from potluck.services.context import AppContext

    assert isinstance(ctx, AppContext)
    _ingest_mixed(ctx)
    sql, params = build_search_sql(
        match='"fennel"',
        kinds=None,
        sources=None,
        from_addrs=["alice@potluck.test"],
        after_iso=None,
        before_iso=None,
        limit=20,
        offset=0,
    )
    with ctx.db.read() as conn:
        plan = "\n".join(str(row[3]) for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params))
    assert "VIRTUAL TABLE INDEX" in plan, plan  # items_fts MATCH drives the scan
    assert "USING INTEGER PRIMARY KEY" in plan, plan  # emails accessed by rowid PK
    assert "SCAN e" not in plan, plan  # never a full emails scan
