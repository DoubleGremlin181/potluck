"""Tests for potluck.services.items: get_item service."""

from pathlib import Path

import pytest

from potluck.core.errors import ItemNotFoundError
from potluck.models.items import ItemKind
from potluck.models.search import SearchRequest
from potluck.services.context import AppContext
from potluck.services.items import get_item
from potluck.services.search import search
from tests.conftest import ingest_keep_corpus

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_item_roundtrip(ctx: AppContext, tmp_path: Path) -> None:
    """get_item returns a fully-hydrated Item matching what was ingested."""
    ingest_keep_corpus(ctx, tmp_path)

    # Get an id via search (any known word from WORDS)
    req = SearchRequest(query="amber", limit=1)
    resp = search(ctx, req)
    assert resp.hits, "Need at least one search hit for roundtrip test"

    hit = resp.hits[0]
    item = get_item(ctx, hit.id)

    assert item.id == hit.id
    assert item.kind == ItemKind.NOTE  # Keep corpus = notes
    assert item.source == "google_keep"
    assert isinstance(item.meta, dict)

    # title and text may be None for list-style or empty notes, but for most notes:
    # At minimum the hit has a snippet, so something was indexed
    # If title was returned in hit, it matches
    if hit.title is not None:
        assert item.title == hit.title


def test_get_item_missing_raises(ctx: AppContext) -> None:
    """get_item raises ItemNotFoundError for a non-existent id."""
    with pytest.raises(ItemNotFoundError):
        get_item(ctx, 999999)
