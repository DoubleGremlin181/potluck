"""Tests for potluck.services.search: search service."""

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from potluck.models.items import ItemKind
from potluck.models.search import SearchRequest
from potluck.services.context import AppContext
from potluck.services.search import search
from tests.conftest import ingest_keep_corpus, insert_import, insert_item, insert_source

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_search_finds_ingested_notes(ctx: AppContext, tmp_path: Path) -> None:
    """After ingesting a Keep corpus, a known word returns hits with populated fields."""
    ingest_keep_corpus(ctx, tmp_path)

    # Pick a word guaranteed to appear in WORDS used by the generator
    req = SearchRequest(query="amber", limit=10)
    resp = search(ctx, req)

    assert resp.query == "amber"
    assert len(resp.hits) > 0, "Expected at least one hit for 'amber'"
    hit = resp.hits[0]
    assert hit.id > 0
    assert hit.kind in list(ItemKind)
    assert hit.snippet != ""
    # Snippet contains bracket markers around matched text
    assert "[" in hit.snippet and "]" in hit.snippet, f"Snippet missing brackets: {hit.snippet!r}"
    assert hit.score < 0.0, "BM25 scores are negative (lower = better match)"


def test_search_hits_carry_source_name(ctx: AppContext, tmp_path: Path) -> None:
    """Hits carry the source name in BOTH branches: BM25-ranked (free text)
    and filter-only (no free text) — the SPA renders a source badge per hit
    without a second lookup (#135)."""
    ingest_keep_corpus(ctx, tmp_path)

    ranked = search(ctx, SearchRequest(query="amber", limit=5))
    assert ranked.hits, "expected ranked hits for 'amber'"
    assert all(hit.source == "google_keep" for hit in ranked.hits)

    filter_only = search(ctx, SearchRequest(query="", kinds=[ItemKind.NOTE], limit=5))
    assert filter_only.hits, "expected filter-only hits for kind=note"
    assert all(hit.source == "google_keep" for hit in filter_only.hits)


def test_search_empty_query_returns_empty(ctx: AppContext, tmp_path: Path) -> None:
    """Empty query string returns empty hits without error."""
    ingest_keep_corpus(ctx, tmp_path)

    req = SearchRequest(query="")
    resp = search(ctx, req)

    assert resp.query == ""
    assert resp.hits == []


def test_search_nonsense_returns_empty(ctx: AppContext, tmp_path: Path) -> None:
    """A query with no matching tokens returns empty hits."""
    ingest_keep_corpus(ctx, tmp_path)

    req = SearchRequest(query="zzzqqqxxx")
    resp = search(ctx, req)

    assert resp.hits == []


def test_title_match_outranks_text_match(ctx: AppContext) -> None:
    """An item matching in the title scores higher (more negative BM25) than text-only match."""
    unique_token = "zirconium"  # unlikely to appear in other test data

    def _setup(conn: sqlite3.Connection) -> tuple[int, int]:
        src = insert_source(conn)
        imp = insert_import(conn, src)
        title_id = insert_item(
            conn,
            src,
            imp,
            title=f"{unique_token} special",
            text="plain unrelated text here",
            content_hash="title-match-hash",
        )
        text_id = insert_item(
            conn,
            src,
            imp,
            title="unrelated heading here",
            text=f"mention of {unique_token} inside text",
            content_hash="text-match-hash",
        )
        return title_id, text_id

    title_id, text_id = ctx.db.write(_setup)

    req = SearchRequest(query=unique_token, limit=10)
    resp = search(ctx, req)

    assert len(resp.hits) == 2, f"Expected 2 hits, got {len(resp.hits)}"
    assert resp.hits[0].id == title_id, (
        f"Title match (id={title_id}) should outrank text match (id={text_id}), "
        f"but first hit is id={resp.hits[0].id}"
    )


def test_kind_filter(ctx: AppContext, tmp_path: Path) -> None:
    """kinds=[ItemKind.NOTE] returns hits; kinds=[ItemKind.EMAIL] returns none (corpus is notes)."""
    ingest_keep_corpus(ctx, tmp_path)

    # NOTE filter: should find results (Keep corpus = notes)
    req_note = SearchRequest(query="amber", kinds=[ItemKind.NOTE], limit=10)
    resp_note = search(ctx, req_note)
    assert len(resp_note.hits) > 0, "Expected NOTE hits for 'amber'"

    # EMAIL filter: should find nothing (no emails in Keep corpus)
    req_email = SearchRequest(query="amber", kinds=[ItemKind.EMAIL], limit=10)
    resp_email = search(ctx, req_email)
    assert resp_email.hits == [], "Expected no EMAIL hits in a Keep corpus"


def test_pagination(ctx: AppContext, tmp_path: Path) -> None:
    """offset=0 and offset=5 return disjoint ids, ordered by score."""
    # 200-note corpus reliably yields >10 hits for "amber" (one of 40 WORDS),
    # making this test deterministic without any corpus-size guard.
    ingest_keep_corpus(ctx, tmp_path, count=200)

    req_page1 = SearchRequest(query="amber", limit=5, offset=0)
    req_page2 = SearchRequest(query="amber", limit=5, offset=5)

    resp1 = search(ctx, req_page1)
    resp2 = search(ctx, req_page2)

    assert len(resp1.hits) == 5, f"Expected full first page (5 hits), got {len(resp1.hits)}"
    assert len(resp2.hits) > 0, "Expected at least one hit on page 2"

    ids1 = {h.id for h in resp1.hits}
    ids2 = {h.id for h in resp2.hits}
    assert ids1.isdisjoint(ids2), "Pages must not overlap"

    # Scores within each page must be non-increasing (lower/more-negative = better)
    scores1 = [h.score for h in resp1.hits]
    assert scores1 == sorted(scores1), "Page 1 hits should be ordered by score"


def test_request_validation() -> None:
    """Invalid request fields raise pydantic.ValidationError."""
    with pytest.raises(ValidationError):
        SearchRequest(query="hello", limit=0)

    with pytest.raises(ValidationError):
        SearchRequest(query="hello", limit=101)

    with pytest.raises(ValidationError):
        SearchRequest(query="hello", offset=-1)


def test_query_max_length_validation() -> None:
    """SearchRequest rejects query strings longer than 1000 characters."""
    with pytest.raises(ValidationError):
        SearchRequest(query="a" * 1001)

    # Exactly 1000 chars is accepted
    req = SearchRequest(query="a" * 1000)
    assert len(req.query) == 1000
