"""Golden test (#142): the committed WhatsApp fixture yields exact results.

The fixture is generator output only (see tests/fixtures/README.md); the
regeneration one-liner lives in potluck/testing/whatsapp.py. Three chats in
three locale dialects (US 12h month-first, EU 24h day-first, iOS bracketed),
40 logical messages each.
"""

from pathlib import Path

from potluck.services.context import AppContext
from potluck.services.imports import import_path

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "whatsapp" / "whatsapp-synth-001"

# 40 logical messages - 2 system lines (i in {3, 23}) per chat, 3 chats.
GOLDEN_MESSAGES_PER_CHAT = 38
GOLDEN_COUNT = 3 * GOLDEN_MESSAGES_PER_CHAT
GOLDEN_MEDIA_REFS = 3 * 3  # attached shape at i in {9, 21, 33} per chat
GOLDEN_MEDIA_MESSAGES = 3 * 6  # + bare placeholders at i in {5, 17, 29}

# Identity stability anchor: the raw-block fingerprint of the US chat's first
# message. If this changes, the parser re-minted identities and every
# existing archive would re-import as duplicates — bump parser_version and
# say so in the commit.
GOLDEN_FIRST_ADA_EID = "wa:32df7c41b443a3d932b46db7a65c585e63cbbd82c7bf9c073da9cbf4d24aa16a"


def test_fixture_exists() -> None:
    assert (FIXTURE / "WhatsApp Chat with Ada Example.txt").is_file()
    assert (FIXTURE / "WhatsApp Chat - Rina Sample" / "_chat.txt").is_file()


def test_golden_import_counts(ctx: AppContext) -> None:
    [run] = import_path(ctx, FIXTURE)
    assert run.source == "whatsapp"
    assert run.status == "completed"
    assert run.items_new == GOLDEN_COUNT
    assert run.items_duplicate == 0

    with ctx.db.read() as conn:
        items = conn.execute("SELECT COUNT(*) FROM items WHERE kind = 'message'").fetchone()[0]
        messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        media_rows = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        media_msgs = conn.execute("SELECT COUNT(*) FROM messages WHERE is_media = 1").fetchone()[0]
    assert items == GOLDEN_COUNT
    assert messages == GOLDEN_COUNT
    assert media_rows == GOLDEN_MEDIA_REFS
    assert media_msgs == GOLDEN_MEDIA_MESSAGES


def test_golden_chats_thread_by_chat_key(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        per_chat = {
            str(r[0]): int(r[1])
            for r in conn.execute(
                "SELECT chat_key, COUNT(*) FROM messages GROUP BY chat_key"
            ).fetchall()
        }
    assert per_chat == {
        "WhatsApp Chat with Ada Example": GOLDEN_MESSAGES_PER_CHAT,
        "WhatsApp Chat with Dana Muster": GOLDEN_MESSAGES_PER_CHAT,
        "WhatsApp Chat - Rina Sample": GOLDEN_MESSAGES_PER_CHAT,
    }


def test_golden_locales_resolve_identical_first_instant(ctx: AppContext) -> None:
    """All three dialects render the same logical schedule: identical first
    timestamps prove month-first, day-first, and bracketed parsing agree."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        firsts = {
            str(r[0])
            for r in conn.execute(
                """SELECT MIN(i.ts) FROM messages m JOIN items i ON i.id = m.item_id
                   GROUP BY m.chat_key"""
            ).fetchall()
        }
    assert firsts == {"2023-03-17T09:00:00+00:00"}


def test_golden_identities_stable(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        eids = {
            str(r[0])
            for r in conn.execute("SELECT external_id FROM items WHERE kind='message'").fetchall()
        }
    assert GOLDEN_FIRST_ADA_EID in eids
    assert all(eid.startswith("wa:") for eid in eids)


def test_golden_reimport_is_noop(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    [run2] = import_path(ctx, FIXTURE)
    assert run2.items_new == 0
    assert run2.items_duplicate == GOLDEN_COUNT
    assert run2.items_updated == 0
