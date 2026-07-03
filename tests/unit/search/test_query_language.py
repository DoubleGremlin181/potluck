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


def test_quoted_value_normalized() -> None:
    """source: values normalize (lowercase, spaces -> underscores) to match
    registered source names — README's own source:"google keep" example must
    actually hit google_keep (#198 review 14)."""
    parsed = parse_query('source:"Google Keep" basil')
    assert parsed.sources == ("google_keep",)
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
    from potluck.models.drafts import NoteDraft
    from potluck.services.context import AppContext
    from tests.conftest import email_draft, ingest_email_drafts

    assert isinstance(ctx, AppContext)
    ingest_email_drafts(
        ctx,
        NoteDraft(title="garden notes", text="basil and fennel layout"),
        source_name="keep-test",
        path="/tmp/t.zip",
    )
    ingest_email_drafts(
        ctx,
        email_draft(
            1,
            message_id="e1@potluck.test",
            from_addr="alice@potluck.test",
            title="garden budget",
            text="fennel seeds invoice",
            ts=datetime(2024, 3, 1, tzinfo=UTC),
        ),
        email_draft(
            2,
            message_id="e2@potluck.test",
            from_addr="bob@potluck.test",
            title="fennel recipe",
            text="roasted fennel with lemon",
            ts=datetime(2025, 3, 1, tzinfo=UTC),
        ),
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


def test_operator_key_requires_token_boundary() -> None:
    """A known key embedded mid-token is NOT an operator: searching for the
    literal 'sent-from:alice@x.com' must not become a from: filter plus the
    junk term 'sent-' (#198 review 16)."""
    parsed = parse_query("sent-from:alice@potluck.test tax")
    assert parsed.from_addrs == ()
    assert "sent-from:alice@potluck.test" in parsed.terms
    assert "tax" in parsed.terms


def test_operator_at_string_start_still_parses() -> None:
    parsed = parse_query("from:alice@potluck.test")
    assert parsed.from_addrs == ("alice@potluck.test",)


def test_source_operator_normalized_end_to_end(ctx: object) -> None:
    from potluck.models.drafts import NoteDraft
    from potluck.models.search import SearchRequest
    from potluck.services.context import AppContext
    from potluck.services.search import search
    from tests.conftest import ingest_email_drafts

    assert isinstance(ctx, AppContext)
    ingest_email_drafts(
        ctx,
        NoteDraft(title="garden notes", text="basil layout"),
        source_name="google_keep",
        path="/tmp/t.zip",
    )
    resp = search(ctx, SearchRequest(query='source:"Google Keep" basil'))
    assert [h.title for h in resp.hits] == ["garden notes"]


def test_operator_errors_surface_as_warnings(ctx: object) -> None:
    """Typo'd filters must not silently broaden the search: the response
    carries warnings on every return path (#198 review 15)."""
    from potluck.models.search import SearchRequest
    from potluck.services.context import AppContext
    from potluck.services.search import search

    assert isinstance(ctx, AppContext)
    _ingest_mixed(ctx)
    resp = search(ctx, SearchRequest(query="kind:emial fennel"))
    assert resp.warnings and "emial" in resp.warnings[0]

    # the early empty-result path carries warnings too
    resp_empty = search(ctx, SearchRequest(query="before:notadate"))
    assert resp_empty.hits == []
    assert resp_empty.warnings


def test_structured_from_addrs_lowercased(ctx: object) -> None:
    """Ingest lowercases from_addr at parse time; the structured field must
    match the inline operator's normalization (#198 review 20)."""
    from potluck.models.search import SearchRequest
    from potluck.services.context import AppContext
    from potluck.services.search import search

    assert isinstance(ctx, AppContext)
    _ingest_mixed(ctx)
    resp = search(ctx, SearchRequest(query="fennel", from_addrs=["ALICE@POTLUCK.TEST"]))
    assert [h.title for h in resp.hits] == ["garden budget"]


def test_structured_naive_dates_pin_utc(ctx: object) -> None:
    """A naive structured after/before is read as UTC — same rows as the
    inline operator, independent of the host timezone (#198 review 20)."""
    from datetime import datetime

    from potluck.models.search import SearchRequest
    from potluck.services.context import AppContext
    from potluck.services.search import search

    assert isinstance(ctx, AppContext)
    _ingest_mixed(ctx)
    inline = search(ctx, SearchRequest(query="kind:email after:2025-01-01 fennel"))
    structured = search(ctx, SearchRequest(query="kind:email fennel", after=datetime(2025, 1, 1)))
    assert [h.id for h in structured.hits] == [h.id for h in inline.hits]
    assert [h.title for h in structured.hits] == ["fennel recipe"]


def test_list_filter_caps() -> None:
    """Unbounded list fields would blow SQLite's host-parameter limit as an
    internal error; the model rejects oversized lists up front (#198 review 19)."""
    import pytest
    from pydantic import ValidationError

    from potluck.models.items import ItemKind
    from potluck.models.search import SearchRequest

    with pytest.raises(ValidationError):
        SearchRequest(query="x", kinds=[ItemKind.EMAIL] * 17)
    with pytest.raises(ValidationError):
        SearchRequest(query="x", sources=["s"] * 65)
    with pytest.raises(ValidationError):
        SearchRequest(query="x", from_addrs=["a@b"] * 65)
