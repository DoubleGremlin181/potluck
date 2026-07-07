"""The MVP user journey as ONE browser test (#140).

Starts from a completely empty app and tells the whole story through the UI:
import a synthetic Takeout zip carrying BOTH Keep notes and Gmail mail, watch
the import complete, search-as-you-type over the fresh corpus, open an email
hit and read the conversation, come back with search state intact, open a
checklist note, and see Settings report the real nonzero counts.

Every action goes through the UI; the API is read only for ground-truth
assertions (result order, thread size, exact counts). The fixture archive is
generated at test time by ``potluck.testing`` with a fixed seed — nothing
binary is committed, and every wait is an ``expect`` on app state.

Runs only with ``-m browser`` (excluded by default); requires ``web/dist``
and Playwright chromium — exactly the CI browser job's environment.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from potluck.testing.archives import write_archive
from potluck.testing.keep import synthetic_keep_notes, write_keep_takeout
from potluck.testing.mbox import write_gmail_takeout
from tests.e2e.conftest import api_get, serve_app

pytestmark = pytest.mark.browser

SEED = 7
NOTES = 30
EMAILS = 60
PAGE_SIZE = 30  # mirrors the UI's search page size

# Deterministic import outcome for (SEED, NOTES): the generated Keep corpus
# carries 2 parser skip targets (trashed/empty notes), so 28 of 30 import.
# Every generated email imports. Pinned exactly — silent item drops must fail
# the test, and UI-vs-API assertions alone cannot see them.
EXPECTED_NOTE_ITEMS = 28

# A WORDS-vocabulary token: with this (seed, corpus) it matches both kinds,
# and the top hit is an email whose thread has more than one message.
SAYT_TERM = "maple"

# Members BOTH product generators emit (parser-ignored decoys — real Takeouts
# have shared members too, e.g. archive_browser.html). Any other collision
# while merging the product trees is a bug and fails loudly below.
SHARED_DECOY_MEMBERS = frozenset({"Takeout/Other/ignored.txt"})


@pytest.fixture
def server_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A fresh EMPTY database per test; XDG_DATA_HOME pinned into tmp so the
    managed uploads directory never touches the developer's real data dir."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    with serve_app(tmp_path / "potluck.db", tmp_path / "config") as url:
        yield url


def write_journey_takeout(dest_dir: Path) -> Path:
    """One Takeout zip with BOTH Keep notes and Gmail mail inside.

    Each generator writes the real member layout for its product into its own
    directory tree; the trees merge member-by-member — mirroring a real
    multi-product Takeout export — into one single-part zip. Any collision
    outside the known shared decoys fails loudly: a meaningful member emitted
    by both generators must never be silently clobbered.
    """
    members: dict[str, bytes] = {}
    for tree in (
        write_keep_takeout(dest_dir / "keep", NOTES, seed=SEED, fmt="dir"),
        write_gmail_takeout(dest_dir / "mail", EMAILS, seed=SEED, fmt="dir"),
    ):
        for path in sorted(tree.rglob("*")):
            if not path.is_file():
                continue
            name = path.relative_to(tree).as_posix()
            assert name not in members or name in SHARED_DECOY_MEMBERS, (
                f"unexpected member collision merging product trees: {name}"
            )
            members[name] = path.read_bytes()
    return write_archive(dest_dir / "takeout-journey-001.zip", members, fmt="zip")


def checklist_note() -> dict[str, Any]:
    """The first titled checklist note in the generated Keep corpus.

    Re-derives the exact notes ``write_keep_takeout`` generated (same count,
    seed, and ratios), giving the journey a deterministic note target whose
    title is the search query and whose items pin the rendered checkboxes.
    """
    for note in synthetic_keep_notes(NOTES, SEED):
        if not note.get("isTrashed") and note.get("listContent") and note.get("title"):
            return dict(note)
    raise AssertionError("generated corpus must contain a titled checklist note")


def test_mvp_user_journey(server_url: str, tmp_path: Path, page: Page) -> None:
    archive = write_journey_takeout(tmp_path / "fixture")

    # --- Empty app: the search page has nothing to show yet -----------------
    page.goto(server_url)
    expect(page.get_by_test_id("empty-idle")).to_be_visible()

    nav = page.get_by_role("navigation", name="Primary")
    nav.get_by_role("link", name="Imports").click()
    expect(page.get_by_test_id("history-empty")).to_be_visible()

    # --- Import: upload the zip, watch progress reach completed -------------
    page.get_by_test_id("upload-input").set_input_files(archive)
    expect(page.get_by_test_id("current-import")).to_be_visible()

    # One zip, two products inside -> one completed ledger row per source.
    expect(page.get_by_test_id("history-row")).to_have_count(2, timeout=15_000)
    gmail_row = page.locator('[data-testid="history-row"][data-source="gmail"]')
    keep_row = page.locator('[data-testid="history-row"][data-source="google_keep"]')
    expect(gmail_row).to_have_attribute("data-status", "completed", timeout=15_000)
    expect(keep_row).to_have_attribute("data-status", "completed")
    expect(page.get_by_test_id("current-import")).to_have_count(0)  # settled

    # Ledger ground truth, pinned to the deterministic expected counts: every
    # generated item imported, none silently dropped, all new on an empty db.
    runs = {run["source"]: run for run in api_get(server_url, "/api/imports")["runs"]}
    assert runs["gmail"]["items_done"] == EMAILS
    assert runs["gmail"]["items_new"] == EMAILS
    assert runs["google_keep"]["items_done"] == EXPECTED_NOTE_ITEMS
    assert runs["google_keep"]["items_new"] == EXPECTED_NOTE_ITEMS
    expect(gmail_row.get_by_test_id("history-items")).to_contain_text(str(EMAILS))
    expect(keep_row.get_by_test_id("history-items")).to_contain_text(str(EXPECTED_NOTE_ITEMS))

    # --- Search: SAYT over the corpus we just imported -----------------------
    nav.get_by_role("link", name="Search").click()
    search_input = page.get_by_test_id("search-input")
    expect(search_input).to_be_focused()  # focused on mount, ready to type
    search_input.fill(SAYT_TERM)  # no Enter: search-as-you-type

    truth = api_get(server_url, "/api/search", q=SAYT_TERM, prefix="true", limit=PAGE_SIZE)
    first = page.get_by_test_id("result-row").first
    expect(first).to_have_attribute("data-item-id", str(truth["hits"][0]["id"]))
    expect(first.locator("mark").first).to_have_text(re.compile(SAYT_TERM, re.IGNORECASE))

    # --- Open an email hit: the whole conversation renders -------------------
    email_hit = next(hit for hit in truth["hits"] if hit["kind"] == "email")
    email_row = page.locator('[data-testid="result-row"][data-kind="email"]').first
    expect(email_row).to_have_attribute("data-item-id", str(email_hit["id"]))
    email_row.click()

    expect(page).to_have_url(re.compile(f"/items/{email_hit['id']}$"))
    expect(page.get_by_test_id("item-title")).to_be_visible()
    thread = api_get(server_url, f"/api/items/{email_hit['id']}/thread")
    assert len(thread["entries"]) >= 2, "journey email must open as a real conversation"
    expect(page.get_by_test_id("thread-message")).to_have_count(len(thread["entries"]))
    focused = page.locator('[data-testid="thread-message"][data-focused]')
    expect(focused).to_have_attribute("data-item-id", str(email_hit["id"]))

    # --- Back: the search state survives the detour ---------------------------
    page.get_by_role("button", name="Back to search").click()
    expect(search_input).to_have_value(SAYT_TERM)
    expect(page.get_by_test_id("result-row").first).to_have_attribute(
        "data-item-id", str(truth["hits"][0]["id"])
    )

    # --- Find and open a note hit: the checklist renders ----------------------
    note = checklist_note()
    note_query = f"kind:note {note['title']}"
    search_input.fill(note_query)

    note_truth = api_get(server_url, "/api/search", q=note_query, prefix="true", limit=PAGE_SIZE)
    assert note_truth["hits"][0]["title"] == note["title"]
    note_row = page.get_by_test_id("result-row").first
    expect(note_row).to_have_attribute("data-item-id", str(note_truth["hits"][0]["id"]))
    expect(note_row).to_have_attribute("data-kind", "note")
    note_row.click()

    expect(page.get_by_test_id("item-title")).to_have_text(str(note["title"]))
    boxes = page.get_by_test_id("note-markdown").locator('input[type="checkbox"]')
    items = note["listContent"]
    expect(boxes).to_have_count(len(items))
    for i, item in enumerate(items):
        if item["isChecked"]:
            expect(boxes.nth(i)).to_be_checked()
        else:
            expect(boxes.nth(i)).not_to_be_checked()
        expect(boxes.nth(i)).to_be_disabled()  # read-only archive view

    # --- Settings: the true nonzero counts for everything we imported ---------
    nav.get_by_role("link", name="Settings").click()
    stats = api_get(server_url, "/api/stats")
    assert stats["items_by_kind"] == {"email": EMAILS, "note": EXPECTED_NOTE_ITEMS}
    email_count = page.locator('[data-testid="kind-count"][data-kind="email"]')
    note_count = page.locator('[data-testid="kind-count"][data-kind="note"]')
    expect(email_count).to_have_attribute("data-count", str(EMAILS))
    expect(note_count).to_have_attribute("data-count", str(EXPECTED_NOTE_ITEMS))
