"""Browser tests for the SPA search page (#134).

Drives the built SPA against a real ``potluck serve`` over a synthetic
corpus (10k generated emails + generated Keep notes — never real data):
search-as-you-type, filter chips, highlight rendering, virtualized infinite
scroll with cursor etiquette, and the designed empty/error states. Ground
truth for result ordering comes from the API itself, so assertions stay
deterministic without hardcoding BM25 ranks.

Runs only with ``-m browser`` (excluded by default); requires ``web/dist``
and Playwright chromium.
"""

import re
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, Route, expect

from potluck.core.config import Settings
from potluck.ingest.engine import run_import
from potluck.services.context import create_context
from potluck.services.imports import import_path
from potluck.testing.keep import write_keep_takeout
from potluck.testing.mbox import synthetic_email_drafts
from tests.e2e.conftest import api_get, serve_app

pytestmark = pytest.mark.browser

# 10k emails: the acceptance criterion corpus for smooth virtualized
# rendering. Common WORDS tokens ("amber", "maple", …) match thousands of
# items, so any of them exercises multi-page cursor walks.
CORPUS_EMAILS = 10_000
SEED = 7
PAGE_SIZE = 30  # mirrors the UI's search page size

INVALID_CURSOR_BODY = (
    '{"error":{"code":"invalid_cursor","message":"cursor does not match this query"}}'
)
SERVER_ERROR_BODY = '{"error":{"code":"internal_error","message":"Internal server error."}}'


@pytest.fixture(scope="module")
def corpus_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """One seeded database + server for the whole module (corpus is read-only)."""
    root = tmp_path_factory.mktemp("search-corpus")
    db_path = root / "potluck.db"
    settings = Settings(
        db_path=db_path,
        extract_attachments=False,
        attachments_dir=root / "attachments",
        uploads_dir=root / "uploads",
    )
    ctx = create_context(settings)
    try:
        # Notes through the real google_keep plugin (source name "google_keep");
        # emails straight into the engine as drafts (source name "gmail") —
        # populating 10k FTS rows in seconds while using the real write path.
        archive = write_keep_takeout(root / "keep", 40, seed=SEED, fmt="dir")
        import_path(ctx, archive)
        run_import(
            ctx.db,
            source_name="gmail",
            parser_version=3,
            drafts=synthetic_email_drafts(CORPUS_EMAILS, seed=SEED),
            path=str(root / "corpus.mbox"),
            file_hash=None,
        )
    finally:
        ctx.db.close()
    with serve_app(db_path, root / "config") as url:
        yield url


def api_search(url: str, **params: Any) -> dict[str, Any]:
    """Ground-truth search results straight from the API."""
    return api_get(url, "/api/search", **params)


def search_input(page: Page) -> Locator:
    return page.get_by_test_id("search-input")


def result_rows(page: Page) -> Locator:
    return page.get_by_test_id("result-row")


# ---------------------------------------------------------------------------
# Search-as-you-type + highlighting
# ---------------------------------------------------------------------------


def test_typing_updates_results_with_highlights(corpus_server: str, page: Page) -> None:
    page.goto(corpus_server)
    expect(search_input(page)).to_be_focused()  # focused on load

    # Type -> debounced prefix search runs without pressing Enter.
    search_input(page).fill("maple")
    truth = api_search(corpus_server, q="maple", prefix="true", limit=PAGE_SIZE)
    first = result_rows(page).first
    expect(first).to_have_attribute("data-item-id", str(truth["hits"][0]["id"]))
    # Highlight markers became real <mark> elements (no raw brackets).
    expect(first.locator("mark").first).to_have_text(re.compile("maple", re.IGNORECASE))
    expect(first).not_to_contain_text("[")

    # Typing more re-runs the search; the last token matches as a prefix
    # ("summ" -> "summit"), so results update mid-word.
    search_input(page).fill("maple summ")
    truth = api_search(corpus_server, q="maple summ", prefix="true", limit=PAGE_SIZE)
    expect(first).to_have_attribute("data-item-id", str(truth["hits"][0]["id"]))
    expect(
        first.locator("mark").filter(has_text=re.compile("summit", re.IGNORECASE)).first
    ).to_be_visible()

    # The URL carries the search state (source of truth).
    expect(page).to_have_url(re.compile(r"\?q=maple\+summ&prefix=1"))


# ---------------------------------------------------------------------------
# Filter chips
# ---------------------------------------------------------------------------


def test_kind_chip_narrows_results(corpus_server: str, page: Page) -> None:
    page.goto(f"{corpus_server}/?q=maple")
    expect(result_rows(page).first).to_be_visible()

    page.get_by_test_id("filter-kind").click()
    page.get_by_role("button", name="note", exact=True).click()
    page.keyboard.press("Escape")  # close the popover

    truth = api_search(corpus_server, q="maple", kind="note", limit=PAGE_SIZE)
    first = result_rows(page).first
    expect(first).to_have_attribute("data-item-id", str(truth["hits"][0]["id"]))
    expect(first).to_have_attribute("data-kind", "note")
    expect(page.locator('[data-testid="result-row"]:not([data-kind="note"])')).to_have_count(0)
    expect(page).to_have_url(re.compile("kind=note"))

    # Removing the chip broadens the search again.
    page.get_by_role("button", name="Remove filter: note").click()
    truth = api_search(corpus_server, q="maple", limit=PAGE_SIZE)
    expect(first).to_have_attribute("data-item-id", str(truth["hits"][0]["id"]))


def test_source_and_date_chips_compose(corpus_server: str, page: Page) -> None:
    # Chips map to the API's repeatable params (not the q string), so they
    # compose with each other; the URL round-trips the full state. All the
    # synthetic keep notes are dated 2020-01-01, so the inclusive `after`
    # bound keeps them while proving the param reaches the API.
    page.goto(f"{corpus_server}/?q=maple&source=google_keep&after=2020-01-01")
    truth = api_search(
        corpus_server, q="maple", source="google_keep", after="2020-01-01", limit=PAGE_SIZE
    )
    assert truth["hits"], "corpus must have keep notes matching maple on/after 2020-01-01"
    first = result_rows(page).first
    expect(first).to_have_attribute("data-item-id", str(truth["hits"][0]["id"]))
    expect(page.locator('[data-testid="result-row"]:not([data-kind="note"])')).to_have_count(0)
    # Both chips render as removable badges.
    expect(page.get_by_role("button", name="Remove filter: google_keep")).to_be_visible()
    expect(page.get_by_role("button", name="Remove filter: after 2020-01-01")).to_be_visible()


# ---------------------------------------------------------------------------
# Virtualized infinite scroll over the 10k corpus
# ---------------------------------------------------------------------------


def test_infinite_scroll_loads_next_page_and_stays_virtualized(
    corpus_server: str, page: Page
) -> None:
    # Ground truth for page 2 of the cursor walk, fetched up front.
    page1 = api_search(corpus_server, q="amber", limit=PAGE_SIZE)
    assert page1["next_cursor"], "corpus must produce more than one page for 'amber'"
    page2 = api_search(corpus_server, q="amber", limit=PAGE_SIZE, cursor=page1["next_cursor"])

    page.goto(f"{corpus_server}/?q=amber")
    scroller = page.get_by_test_id("results-scroll")
    expect(scroller).to_have_attribute("data-loaded-count", str(PAGE_SIZE))

    # Scroll to the bottom: the sentinel row fetches page 2 via next_cursor,
    # and the first hit of page 2 renders right below the old fold — the UI
    # walk matches the API walk exactly.
    scroller.evaluate("el => { el.scrollTop = el.scrollHeight }")
    expect(
        page.locator(f'[data-testid="result-row"][data-item-id="{page2["hits"][0]["id"]}"]')
    ).to_be_visible(timeout=10_000)

    # More pages loaded (the sentinel may cascade past 2 pages)…
    loaded = int(scroller.get_attribute("data-loaded-count") or "0")
    assert loaded >= PAGE_SIZE * 2
    # …while the DOM holds a bounded virtualized window, not every loaded hit.
    assert result_rows(page).count() < loaded


def test_invalid_cursor_restarts_walk_instead_of_erroring(corpus_server: str, page: Page) -> None:
    # Force every cursor-bearing request to 400 invalid_cursor: the UI must
    # restart from the first page with a notice — not show an error state —
    # and it must NOT loop: exactly one automatic restart per search, then a
    # manual "Restart search" affordance.
    cursor_requests: list[str] = []

    def reject_cursors(route: Route) -> None:
        if "cursor=" in route.request.url:
            cursor_requests.append(route.request.url)
            route.fulfill(status=400, content_type="application/json", body=INVALID_CURSOR_BODY)
        else:
            route.continue_()

    page.route("**/api/search*", reject_cursors)
    page.goto(f"{corpus_server}/?q=amber")
    scroller = page.get_by_test_id("results-scroll")
    expect(scroller).to_have_attribute("data-loaded-count", str(PAGE_SIZE))

    # First rejection: automatic restart with a transient notice, scrolled
    # back to the top (so the sentinel isn't instantly visible again).
    scroller.evaluate("el => { el.scrollTop = el.scrollHeight }")
    expect(page.get_by_test_id("cursor-reset-banner")).to_be_visible()
    expect(page.get_by_test_id("cursor-reset-banner")).to_contain_text("restarting")
    expect(page.get_by_test_id("error-state")).to_have_count(0)
    assert scroller.evaluate("el => el.scrollTop") == 0
    expect(scroller).to_have_attribute("data-loaded-count", str(PAGE_SIZE))

    # Second rejection: the automatic reset is capped — the UI offers a
    # manual restart instead of looping reset -> refetch -> reject.
    scroller.evaluate("el => { el.scrollTop = el.scrollHeight }")
    stuck = page.get_by_test_id("cursor-stuck-banner")
    expect(stuck).to_be_visible()
    expect(page.get_by_test_id("error-state")).to_have_count(0)
    seen = len(cursor_requests)
    page.wait_for_timeout(1_500)  # a runaway loop would fire many requests here
    assert len(cursor_requests) - seen <= 1, f"cursor request loop ran away: {cursor_requests}"
    assert len(cursor_requests) <= 3

    # The manual restart resumes the walk once the server behaves again.
    page.unroute("**/api/search*")
    stuck.get_by_role("button", name="Restart search").click()
    expect(stuck).to_have_count(0)
    expect(scroller).to_have_attribute("data-loaded-count", str(PAGE_SIZE), timeout=10_000)
    scroller.evaluate("el => { el.scrollTop = el.scrollHeight }")
    expect(scroller).not_to_have_attribute("data-loaded-count", str(PAGE_SIZE), timeout=15_000)


# ---------------------------------------------------------------------------
# Empty, warning, and error states
# ---------------------------------------------------------------------------


def test_empty_states(corpus_server: str, page: Page) -> None:
    page.goto(corpus_server)
    # No query yet: hint text with query-language examples.
    expect(page.get_by_test_id("empty-idle")).to_be_visible()
    expect(page.get_by_test_id("empty-idle")).to_contain_text("kind:")

    # No results: suggest removing filters (a filter is active).
    page.goto(f"{corpus_server}/?q=xylophone&kind=email")
    empty = page.get_by_test_id("empty-no-results")
    expect(empty).to_be_visible()
    expect(empty).to_contain_text("No results for “xylophone”")
    empty.get_by_role("button", name="Clear filters").click()
    expect(page).not_to_have_url(re.compile("kind=email"))


def test_operator_warning_is_dismissible(corpus_server: str, page: Page) -> None:
    # A typo'd inline operator is dropped server-side and reported inline.
    page.goto(f"{corpus_server}/?q=kind:emial%20maple")
    banner = page.get_by_test_id("warning-banner")
    expect(banner).to_contain_text("unknown kind 'emial'")
    expect(result_rows(page).first).to_be_visible()  # search still ran
    banner.get_by_role("button", name="Dismiss warnings").click()
    expect(banner).to_have_count(0)


def test_error_state_offers_retry(corpus_server: str, page: Page) -> None:
    page.route(
        "**/api/search*",
        lambda route: route.fulfill(
            status=500, content_type="application/json", body=SERVER_ERROR_BODY
        ),
    )
    page.goto(f"{corpus_server}/?q=maple")
    error = page.get_by_test_id("error-state")
    expect(error).to_be_visible()
    expect(error).to_contain_text("Internal server error.")

    page.unroute("**/api/search*")
    error.get_by_role("button", name="Try again").click()
    expect(result_rows(page).first).to_be_visible()


# ---------------------------------------------------------------------------
# Navigation: detail placeholder, URL-as-state, "/" shortcut
# ---------------------------------------------------------------------------


def test_click_hit_opens_item_and_back_restores_search(corpus_server: str, page: Page) -> None:
    page.goto(f"{corpus_server}/?q=maple&kind=email")
    first = result_rows(page).first
    expect(first).to_be_visible()
    item_id = first.get_attribute("data-item-id")
    assert item_id is not None

    first.click()
    expect(page).to_have_url(re.compile(f"/items/{item_id}$"))
    expect(page.get_by_test_id("item-title")).to_be_visible()

    # Back restores the full search state from the URL.
    page.go_back()
    expect(search_input(page)).to_have_value("maple")
    expect(page.get_by_role("button", name="Remove filter: email")).to_be_visible()
    expect(result_rows(page).first).to_have_attribute("data-item-id", item_id)


def test_slash_focuses_search_from_anywhere(corpus_server: str, page: Page) -> None:
    page.goto(f"{corpus_server}/")
    page.get_by_role("navigation", name="Primary").get_by_role("link", name="Settings").click()
    expect(page.get_by_role("heading", name="Settings")).to_be_visible()

    page.keyboard.press("/")
    expect(search_input(page)).to_be_focused()
    expect(page.get_by_test_id("empty-idle")).to_be_visible()
