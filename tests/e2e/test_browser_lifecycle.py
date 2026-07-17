"""Browser tests for the imports-page delete flow (#153).

Drives the built SPA against a real ``potluck serve``: the per-row delete
button, the confirm dialog offering "Delete" vs "Delete & forget", cancel
safety (a destructive action is never one accidental keypress away), and the
forget → re-import → suppressed-counter round trip.

Runs only with ``-m browser`` (excluded by default); requires ``web/dist``
and Playwright chromium.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from potluck.core.config import Settings
from potluck.services.context import create_context
from potluck.services.imports import import_path
from potluck.testing.keep import write_keep_takeout
from tests.e2e.conftest import api_get, serve_app

pytestmark = pytest.mark.browser

SEED = 7
COUNT = 12
EXPECTED_NEW = 11  # golden corpus: 1 trashed note skipped by the parser


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, Path]]:
    """A server over a database seeded with one completed Keep import;
    yields (url, archive_path) so tests can re-import the same archive."""
    archive = write_keep_takeout(tmp_path / "keep", COUNT, seed=SEED, fmt="zip")
    db_path = tmp_path / "potluck.db"
    ctx = create_context(Settings(db_path=db_path))
    try:
        [run] = import_path(ctx, archive)
        assert run.items_new == EXPECTED_NEW
    finally:
        ctx.db.close()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    with serve_app(db_path, tmp_path / "config") as url:
        yield url, archive


def test_delete_dialog_cancel_then_delete(seeded: tuple[str, Path], page: Page) -> None:
    url, _archive = seeded
    page.goto(f"{url}/imports")
    row = page.get_by_test_id("history-row")
    expect(row).to_have_count(1)

    # The delete needs TWO deliberate actions: the row button, then the
    # dialog's confirm — and Cancel backs out without touching anything.
    row.get_by_test_id("history-delete").click()
    dialog = page.get_by_test_id("delete-dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("google_keep")
    expect(dialog.get_by_test_id("confirm-delete")).to_be_visible()
    expect(dialog.get_by_test_id("confirm-forget")).to_be_visible()
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_have_count(0)
    expect(row).to_have_count(1)
    assert api_get(url, "/api/imports")["total"] == 1

    # Confirmed delete: row gone, ledger and items gone (API = ground truth).
    row.get_by_test_id("history-delete").click()
    page.get_by_test_id("confirm-delete").click()
    expect(page.get_by_test_id("history-empty")).to_be_visible()
    assert api_get(url, "/api/imports")["total"] == 0
    assert api_get(url, "/api/stats")["items"] == 0


def test_forget_blocks_reimport_with_suppressed_counter(
    seeded: tuple[str, Path], page: Page
) -> None:
    url, archive = seeded
    page.goto(f"{url}/imports")

    page.get_by_test_id("history-delete").click()
    page.get_by_test_id("confirm-forget").click()
    expect(page.get_by_test_id("history-empty")).to_be_visible()
    assert api_get(url, "/api/stats")["items"] == 0

    # Re-import the same archive through the UI: every draft is suppressed —
    # nothing comes back, and the run says so in its counters.
    page.get_by_test_id("path-input").fill(str(archive))
    page.get_by_test_id("path-import-button").click()
    row = page.get_by_test_id("history-row").first
    expect(row).to_have_attribute("data-status", "completed", timeout=15_000)
    expect(row.get_by_test_id("history-items")).to_contain_text(f"{EXPECTED_NEW} suppressed")

    truth = api_get(url, "/api/imports")["runs"][0]
    assert truth["items_suppressed"] == EXPECTED_NEW
    assert truth["items_new"] == 0
    assert api_get(url, "/api/stats")["items"] == 0


def test_delete_button_disabled_while_running(seeded: tuple[str, Path], page: Page) -> None:
    """A running run's delete button is disabled (the API would 409 anyway)."""
    url, _archive = seeded
    page.goto(f"{url}/imports")
    # Force the row into 'running' via an intercepted history response — a
    # real running import over a synthetic archive settles in milliseconds.
    page.route(
        "**/api/imports?*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=(
                '{"runs":[{"id":1,"source":"google_keep","path":"/tmp/x.zip",'
                '"file_hash":null,"parser_version":1,'
                '"started_at":"2026-07-16T00:00:00+00:00","finished_at":null,'
                '"status":"running","items_new":5,"items_duplicate":0,'
                '"items_updated":0,"items_skipped":0,"items_suppressed":0,'
                '"items_total":null,"error":null,"extract_attachments":false,'
                '"items_done":5}],"total":1}'
            ),
        ),
    )
    page.reload()
    row = page.get_by_test_id("history-row")
    expect(row).to_have_attribute("data-status", "running")
    expect(row.get_by_test_id("history-delete")).to_be_disabled()
