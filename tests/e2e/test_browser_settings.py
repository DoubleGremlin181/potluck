"""Browser tests for the SPA settings page (#137).

Drives the built SPA against a real ``potluck serve`` over a small seeded
database and asserts the page shows REAL values end to end — the served
file's actual path, true per-kind counts, and the running package version —
never mocked API responses.

Runs only with ``-m browser`` (excluded by default); requires ``web/dist``
and Playwright chromium.
"""

from collections.abc import Iterator

import httpx
import pytest
from playwright.sync_api import BrowserContext, Page, expect

from potluck.core.config import Settings
from potluck.services.context import create_context
from potluck.services.imports import import_path
from potluck.testing.keep import write_keep_takeout
from tests.conftest import email_draft, ingest_email_drafts
from tests.e2e.conftest import serve_app

pytestmark = pytest.mark.browser

# 7 notes vs 3 emails: unequal counts prove per-kind grouping (not just the
# total) and pin the largest-first ordering of the breakdown.
NOTES = 7
EMAILS = 3
SEED = 11


@pytest.fixture(scope="module")
def settings_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    """A served database seeded with 7 notes + 3 emails; yields (url, db_path)."""
    root = tmp_path_factory.mktemp("settings-fixture")
    db_path = root / "potluck.db"
    settings = Settings(
        db_path=db_path,
        extract_attachments=False,
        attachments_dir=root / "attachments",
        uploads_dir=root / "uploads",
    )
    ctx = create_context(settings)
    try:
        archive = write_keep_takeout(root / "keep", NOTES, seed=SEED, fmt="dir")
        import_path(ctx, archive)
        ingest_email_drafts(
            ctx, *(email_draft(n) for n in range(1, EMAILS + 1)), source_name="gmail"
        )
    finally:
        ctx.db.close()
    with serve_app(db_path, root / "config") as url:
        yield url, str(db_path)


def api_json(url: str, path: str) -> dict[str, object]:
    resp = httpx.get(f"{url}{path}", timeout=10.0)
    resp.raise_for_status()
    return dict(resp.json())


def test_database_section_shows_real_path_and_version(
    settings_server: tuple[str, str], page: Page, context: BrowserContext
) -> None:
    url, db_path = settings_server
    # Ground truth: the version the running package actually serves.
    health = api_json(url, "/api/health")

    page.goto(f"{url}/settings")
    # The served database file's real path, verbatim.
    expect(page.get_by_test_id("db-path")).to_have_text(db_path)
    # About shows the exact package version the API reports.
    expect(page.get_by_test_id("app-version")).to_have_text(str(health["version"]))

    # The path is copyable: the copy button puts the verbatim path on the
    # clipboard (permissions granted explicitly — headless denies by default).
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=url)
    page.get_by_role("button", name="Copy database path").click()
    assert page.evaluate("() => navigator.clipboard.readText()") == db_path


def test_items_by_kind_shows_true_counts(settings_server: tuple[str, str], page: Page) -> None:
    url, _ = settings_server
    stats = api_json(url, "/api/stats")
    assert stats["items_by_kind"] == {"note": NOTES, "email": EMAILS}

    page.goto(f"{url}/settings")
    rows = page.get_by_test_id("kind-count")
    expect(rows).to_have_count(2)  # only nonzero kinds get a row
    # Largest kind first, each with its true count.
    expect(rows.first).to_have_attribute("data-kind", "note")
    expect(rows.first).to_have_attribute("data-count", str(NOTES))
    expect(rows.first).to_contain_text(str(NOTES))
    email_row = page.locator('[data-testid="kind-count"][data-kind="email"]')
    expect(email_row).to_have_attribute("data-count", str(EMAILS))
    # The ten empty kinds fold into one muted summary line.
    summary = page.get_by_test_id("zero-kinds")
    expect(summary).to_contain_text("photo")
    expect(summary).to_contain_text("transaction")


def test_sources_and_enrichment_sections(settings_server: tuple[str, str], page: Page) -> None:
    url, _ = settings_server
    resp = httpx.get(f"{url}/api/sources", timeout=10.0)
    resp.raise_for_status()
    sources = resp.json()
    assert sources, "at least the built-in plugins must be registered"

    page.goto(f"{url}/settings")
    # Every registered plugin renders a row with its kind badges.
    for source in sources:
        row = page.locator(f'[data-testid="source-row"][data-source="{source["name"]}"]')
        expect(row).to_be_visible()
        for kind in source["kinds"]:
            expect(row).to_contain_text(kind)
    # Enrichment is an honest placeholder until the embedding phase lands.
    expect(page.get_by_test_id("enrichment-placeholder")).to_contain_text("Not enabled yet")


def test_theme_buttons_switch_appearance(settings_server: tuple[str, str], page: Page) -> None:
    url, _ = settings_server
    page.goto(f"{url}/settings")
    html = page.locator("html")

    page.get_by_role("button", name="Dark").click()
    expect(html).to_have_class("dark")
    page.get_by_role("button", name="Light").click()
    expect(html).to_have_class("light")
