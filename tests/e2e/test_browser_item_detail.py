"""Browser tests for the SPA item detail page (#135).

Drives the built SPA against a real ``potluck serve`` over a hand-seeded
synthetic corpus: the email view renders the whole conversation in thread
order with the focused message marked, long threads collapse behind an
"older messages" expander, note views render markdown/GFM checklists, back
restores the search page state, deep links survive hard reloads (the SPA
fallback), and a missing item shows the designed not-found state.

Ground truth for thread order comes from the API itself. Runs only with
``-m browser``; requires ``web/dist`` and Playwright chromium.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from playwright.sync_api import Page, expect

from potluck.core.config import Settings
from potluck.models.drafts import EmailAttachment, NoteDraft
from potluck.services.context import create_context
from tests.conftest import email_draft, ingest_email_drafts
from tests.e2e.conftest import serve_app

pytestmark = pytest.mark.browser

_T0 = datetime(2024, 3, 1, 9, 0, tzinfo=UTC)

# Unique searchable tokens (never in the shared WORDS vocabulary).
SHORT_TOKEN = "zephyrite"
LONG_TOKEN = "obsidianflow"

LONG_THREAD_SIZE = 12
THREAD_TAIL = 5  # mirrors the UI's long-thread collapse window

CHECKLIST_TEXT = "Trailhead notes for the *big* trip.\n\n- [x] tent\n- [ ] stove\n- [x] lantern\n"


@pytest.fixture(scope="module")
def detail_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """One seeded database + server for the whole module (corpus is read-only)."""
    root = tmp_path_factory.mktemp("item-detail")
    db_path = root / "potluck.db"
    settings = Settings(
        db_path=db_path,
        extract_attachments=False,
        attachments_dir=root / "attachments",
        uploads_dir=root / "uploads",
    )
    ctx = create_context(settings)
    try:
        short_thread = [
            email_draft(
                1,
                thread_key="zeph@potluck.test",
                message_id="zeph1@potluck.test",
                ts=_T0,
                title=f"{SHORT_TOKEN} kickoff",
                text=f"Planning the {SHORT_TOKEN} survey.\n\nFirst message.",
                from_addr="alice@potluck.test",
                from_name="Alice Quartz",
                to_addrs=("bob@potluck.test",),
                to_names=("Bob Basalt",),
                cc_addrs=("carol@example.com",),
                cc_names=("",),
                labels=("Inbox", "Geology"),
            ),
            email_draft(
                2,
                thread_key="zeph@potluck.test",
                message_id="zeph2@potluck.test",
                in_reply_to="zeph1@potluck.test",
                ts=_T0 + timedelta(hours=1),
                title=f"Re: {SHORT_TOKEN} kickoff",
                text="Attaching the borehole readings.",
                from_addr="bob@potluck.test",
                from_name="Bob Basalt",
                to_addrs=("alice@potluck.test",),
                to_names=("Alice Quartz",),
                attachments=(
                    EmailAttachment(
                        filename="readings.csv",
                        mime="text/csv",
                        size_bytes=2048,
                        sha256="ab" * 32,
                    ),
                ),
            ),
            email_draft(
                3,
                thread_key="zeph@potluck.test",
                message_id="zeph3@potluck.test",
                in_reply_to="zeph2@potluck.test",
                ts=_T0 + timedelta(hours=2),
                title=f"Re: {SHORT_TOKEN} kickoff",
                text=f"Final thoughts: the {SHORT_TOKEN} summit review looks solid.",
                from_addr="carol@example.com",
                from_name="Carol Chert",
                to_addrs=("alice@potluck.test", "bob@potluck.test"),
                to_names=("Alice Quartz", "Bob Basalt"),
                labels=("Inbox",),
            ),
        ]
        long_thread = [
            email_draft(
                10 + n,
                thread_key="obsidian@potluck.test",
                message_id=f"obs{n}@potluck.test",
                in_reply_to=f"obs{n - 1}@potluck.test" if n > 0 else None,
                ts=_T0 + timedelta(days=1, minutes=n * 30),
                title=f"{LONG_TOKEN} rollout" if n == 0 else f"Re: {LONG_TOKEN} rollout",
                text=f"Update number {n} on the {LONG_TOKEN} rollout.",
                from_addr=f"dev{n}@potluck.test",
            )
            for n in range(LONG_THREAD_SIZE)
        ]
        note = NoteDraft(
            external_id="note:packing",
            title="Packing checklist zugspitze",
            text=CHECKLIST_TEXT,
            ts=_T0 + timedelta(days=2),
        )
        ingest_email_drafts(ctx, *short_thread, *long_thread, note, source_name="gmail")
    finally:
        ctx.db.close()
    with serve_app(db_path, root / "config") as url:
        yield url


def api_get(url: str, path: str, **params: Any) -> dict[str, Any]:
    resp = httpx.get(f"{url}{path}", params=params, timeout=10.0)
    resp.raise_for_status()
    return dict(resp.json())


def hit_id(url: str, query: str) -> int:
    hits = api_get(url, "/api/search", q=query)["hits"]
    assert hits, f"corpus must have a hit for {query!r}"
    return int(hits[0]["id"])


def messages(page: Page) -> Any:
    return page.get_by_test_id("thread-message")


# ---------------------------------------------------------------------------
# Email view: open a result -> the conversation renders in thread order
# ---------------------------------------------------------------------------


def test_open_result_renders_thread_in_order(detail_server: str, page: Page) -> None:
    page.goto(f"{detail_server}/?q={SHORT_TOKEN}+summit")
    first = page.get_by_test_id("result-row").first
    expect(first).to_be_visible()
    # The search row carries its source badge (SearchHit.source, #135 fix B).
    expect(first.get_by_test_id("result-source")).to_have_text("gmail")
    clicked_id = first.get_attribute("data-item-id")
    assert clicked_id is not None
    first.click()

    expect(page).to_have_url(f"{detail_server}/items/{clicked_id}")
    truth = api_get(detail_server, f"/api/items/{clicked_id}/thread")
    order = [str(entry["id"]) for entry in truth["entries"]]
    assert len(order) == 3

    # Every message renders, in the API's oldest-first order.
    expect(messages(page)).to_have_count(3)
    rendered = [messages(page).nth(i).get_attribute("data-item-id") for i in range(len(order))]
    assert rendered == order

    # The opened item is visually marked and expanded with its full body...
    focused = page.locator('[data-testid="thread-message"][data-focused]')
    expect(focused).to_have_attribute("data-item-id", clicked_id)
    expect(focused.get_by_test_id("message-text")).to_contain_text("summit review")
    # ...while other messages sit collapsed showing sender + preview (thread
    # entries carry the bare address; display names arrive with the detail).
    root = messages(page).first
    expect(root).to_have_attribute("data-expanded", "false")
    expect(root).to_contain_text("alice@potluck.test")
    expect(root).to_contain_text("Planning the zephyrite survey.")


def test_expanding_a_message_shows_recipients_labels_attachments(
    detail_server: str, page: Page
) -> None:
    focused_id = hit_id(detail_server, f"{SHORT_TOKEN} summit")
    page.goto(f"{detail_server}/items/{focused_id}")
    middle = messages(page).nth(1)  # Bob's reply, collapsed
    expect(middle).to_have_attribute("data-expanded", "false")
    middle.get_by_role("button").first.click()

    # Full body replaces the preview; to/cc live under the details toggle.
    expect(middle.get_by_test_id("message-text")).to_have_text("Attaching the borehole readings.")
    recipients = middle.get_by_test_id("message-recipients")
    recipients.locator("summary").click()
    expect(recipients).to_contain_text("Alice Quartz <alice@potluck.test>")

    # Attachment metadata renders: name, mime, human-readable size.
    attachments = middle.get_by_test_id("message-attachments")
    expect(attachments).to_contain_text("readings.csv")
    expect(attachments).to_contain_text("text/csv · 2.0 KiB")

    # Labels render as badges on the focused (expanded) message.
    focused = page.locator('[data-testid="thread-message"][data-focused]')
    expect(focused).to_contain_text("Inbox")


# ---------------------------------------------------------------------------
# Long threads collapse client-side
# ---------------------------------------------------------------------------


def test_long_thread_collapses_and_expands(detail_server: str, page: Page) -> None:
    focused_id = hit_id(detail_server, f"{LONG_TOKEN} update number 11")
    truth = api_get(detail_server, f"/api/items/{focused_id}/thread")
    order = [str(entry["id"]) for entry in truth["entries"]]
    assert len(order) == LONG_THREAD_SIZE

    page.goto(f"{detail_server}/items/{focused_id}")

    # Collapsed: first message + the latest five VISIBLE (the middle stays
    # mounted but display:none, so expanding never resets per-message state),
    # with the expander in between.
    visible_messages = page.locator('[data-testid="thread-message"]:visible')
    expect(visible_messages).to_have_count(1 + THREAD_TAIL)
    expander = page.get_by_test_id("thread-older-expander")
    hidden = LONG_THREAD_SIZE - 1 - THREAD_TAIL
    expect(expander).to_contain_text(f"{hidden} older messages")
    visible = [
        visible_messages.nth(i).get_attribute("data-item-id") for i in range(1 + THREAD_TAIL)
    ]
    assert visible == [order[0], *order[-THREAD_TAIL:]]

    # Expanding reveals the full conversation in order (client-side: no new
    # thread request is needed, the API already returned everything).
    expander.click()
    expect(visible_messages).to_have_count(LONG_THREAD_SIZE)
    expect(page.get_by_test_id("thread-older-expander")).to_have_count(0)
    rendered = [
        visible_messages.nth(i).get_attribute("data-item-id") for i in range(LONG_THREAD_SIZE)
    ]
    assert rendered == order


def test_long_thread_never_hides_the_focused_message(detail_server: str, page: Page) -> None:
    """Opening a message that lives in the collapsible middle starts the
    thread fully expanded — the item the user clicked is always on screen."""
    focused_id = hit_id(detail_server, f"{LONG_TOKEN} update number 3")
    page.goto(f"{detail_server}/items/{focused_id}")

    visible_messages = page.locator('[data-testid="thread-message"]:visible')
    expect(visible_messages).to_have_count(LONG_THREAD_SIZE)
    expect(page.get_by_test_id("thread-older-expander")).to_have_count(0)
    focused = page.locator('[data-testid="thread-message"][data-focused]')
    expect(focused).to_be_visible()
    expect(focused).to_have_attribute("data-item-id", str(focused_id))


# ---------------------------------------------------------------------------
# Note view: markdown + GFM checklists
# ---------------------------------------------------------------------------


def test_note_renders_markdown_checklist(detail_server: str, page: Page) -> None:
    note_id = hit_id(detail_server, "zugspitze")
    page.goto(f"{detail_server}/items/{note_id}")

    note = page.get_by_test_id("note-markdown")
    expect(note).to_be_visible()
    boxes = note.locator('input[type="checkbox"]')
    expect(boxes).to_have_count(3)
    expect(boxes.nth(0)).to_be_checked()
    expect(boxes.nth(1)).not_to_be_checked()
    expect(boxes.nth(2)).to_be_checked()
    for i in range(3):
        expect(boxes.nth(i)).to_be_disabled()
    expect(note).to_contain_text("stove")
    # Inline markdown renders as real elements (no raw asterisks).
    expect(note.locator("em")).to_have_text("big")

    # The meta inspector pretty-prints the raw meta JSON on demand.
    inspector = page.get_by_test_id("meta-inspector")
    inspector.get_by_role("button", name="Meta").click()
    expect(page.get_by_test_id("meta-json")).to_be_visible()


# ---------------------------------------------------------------------------
# Back preserves search state; deep links survive reloads; 404 state
# ---------------------------------------------------------------------------


def test_back_restores_query_filters_and_scroll(detail_server: str, page: Page) -> None:
    page.goto(f"{detail_server}/?q={LONG_TOKEN}&kind=email")
    rows = page.get_by_test_id("result-row")
    expect(rows.first).to_be_visible()

    # Scroll partway down the results, then open a hit that is visible at
    # that offset (clicking an off-screen row would auto-scroll and disturb
    # the position we want restored).
    scroller = page.get_by_test_id("results-scroll")
    scroller.evaluate("el => { el.scrollTop = 300 }")
    target = rows.nth(4)
    expect(target).to_be_in_viewport()
    target.click()
    expect(page.get_by_test_id("item-title")).to_be_visible()

    # The page's own back affordance pops history: query, filter chips, and
    # (roughly) the scroll offset all come back.
    page.get_by_role("button", name="Back to search").click()
    expect(page.get_by_test_id("search-input")).to_have_value(LONG_TOKEN)
    expect(page.get_by_role("button", name="Remove filter: email")).to_be_visible()
    expect(rows.first).to_be_visible()
    # "Reasonably": the offset is restored before the virtualizer re-measures
    # row heights, so it may clamp a few pixels short of the exact value.
    restored = scroller.evaluate("el => el.scrollTop")
    assert 200 <= restored <= 320, f"scroll not restored: {restored}"


def test_deep_link_hard_reload_serves_the_app(detail_server: str, page: Page) -> None:
    """A client route fetched straight from the server (no SPA navigation)
    must serve the app shell — the #135 SPA fallback — and then render."""
    note_id = hit_id(detail_server, "zugspitze")

    resp = httpx.get(f"{detail_server}/items/{note_id}", timeout=10.0)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]

    page.goto(f"{detail_server}/items/{note_id}")
    expect(page.get_by_test_id("item-title")).to_have_text("Packing checklist zugspitze")
    page.reload()
    expect(page.get_by_test_id("item-title")).to_have_text("Packing checklist zugspitze")


def test_missing_item_shows_not_found_state(detail_server: str, page: Page) -> None:
    page.goto(f"{detail_server}/items/999999")
    not_found = page.get_by_test_id("item-not-found")
    expect(not_found).to_be_visible()
    expect(not_found).to_contain_text("Item not found")
    not_found.get_by_role("link", name="Back to search").click()
    expect(page.get_by_test_id("empty-idle")).to_be_visible()
