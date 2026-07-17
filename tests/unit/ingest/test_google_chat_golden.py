"""Golden test (#147): the committed Google Chat fixture yields exact results.

The fixture is generator output only (see tests/fixtures/README.md); the
regeneration one-liner lives in potluck/testing/google_chat.py. Two populated
groups (a DM and a named Space, 40 logical records each), one empty DM, and
a Users/ sidecar carrying the export owner.
"""

from pathlib import Path

from potluck.services.context import AppContext
from potluck.services.imports import import_path

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "google_chat" / "google-chat-synth-001"

# 40 logical records - 2 membership stubs (i in {3, 23}) per populated group.
GOLDEN_MESSAGES_PER_GROUP = 38
GOLDEN_COUNT = 2 * GOLDEN_MESSAGES_PER_GROUP
GOLDEN_MEDIA_REFS = 2 * 2  # attachment records at i in {9, 21} per group
GOLDEN_DUPLICATE_SUFFIXES = 2 * 2  # verbatim copies at i in {16, 33} per group

# Identity stability anchor: the exported message_id of the DM's first
# message. If this changes, the parser re-minted identities and every
# existing archive would re-import as duplicates — bump parser_version and
# say so in the commit.
GOLDEN_FIRST_DM_EID = "gchat:synthdm01AAAE/syntopic-0000/synmsg-0000"


def test_fixture_exists() -> None:
    groups = FIXTURE / "Takeout" / "Google Chat" / "Groups"
    assert (groups / "DM synthdm01AAAE" / "messages.json").is_file()
    assert (groups / "DM synthdm01AAAE" / "group_info.json").is_file()
    assert (groups / "Space AAAAsynthsp1" / "messages.json").is_file()


def test_golden_import_counts(ctx: AppContext) -> None:
    [run] = import_path(ctx, FIXTURE)
    assert run.source == "google_chat"
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
    assert media_msgs == GOLDEN_MEDIA_REFS


def test_golden_dm_and_space_thread_by_group_dir(ctx: AppContext) -> None:
    """Both group flavors parse (the #147 acceptance criterion): chat_key is
    the group directory name, whose prefix discriminates DM from Space; the
    empty DM imports nothing and no phantom chat appears."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        per_chat = {
            str(r[0]): int(r[1])
            for r in conn.execute(
                "SELECT chat_key, COUNT(*) FROM messages GROUP BY chat_key"
            ).fetchall()
        }
    assert per_chat == {
        "DM synthdm01AAAE": GOLDEN_MESSAGES_PER_GROUP,
        "Space AAAAsynthsp1": GOLDEN_MESSAGES_PER_GROUP,
    }


def test_golden_chat_names_from_sidecars(ctx: AppContext) -> None:
    """DM: the other participant's display name (owner from user_info.json
    excluded); Space: the room name from group_info.json."""
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        names = {
            str(r[0]): str(r[1])
            for r in conn.execute("SELECT DISTINCT chat_key, chat_name FROM messages").fetchall()
        }
    assert names == {
        "DM synthdm01AAAE": "Bo Sample",
        "Space AAAAsynthsp1": "Synthetic Fixture Crew",
    }


def test_golden_senders_and_meta_email(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        senders = {
            str(r[0]) for r in conn.execute("SELECT DISTINCT sender FROM messages").fetchall()
        }
        with_email = conn.execute(
            "SELECT COUNT(*) FROM items WHERE kind = 'message'"
            " AND json_extract(meta, '$.sender_email') LIKE '%@potluck.test'"
        ).fetchone()[0]
    assert senders == {"Ada Example", "Bo Sample", "Cy Test"}
    assert with_email == GOLDEN_COUNT


def test_golden_identities_and_first_instant(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    with ctx.db.read() as conn:
        eids = {
            str(r[0])
            for r in conn.execute("SELECT external_id FROM items WHERE kind='message'").fetchall()
        }
        firsts = {
            str(r[0])
            for r in conn.execute(
                """SELECT MIN(i.ts) FROM messages m JOIN items i ON i.id = m.item_id
                   GROUP BY m.chat_key"""
            ).fetchall()
        }
    assert GOLDEN_FIRST_DM_EID in eids
    assert all(eid.startswith("gchat:") for eid in eids)
    assert sum("#" in eid for eid in eids) == GOLDEN_DUPLICATE_SUFFIXES
    # Both groups render the same logical schedule from the same base instant.
    assert firsts == {"2023-03-17T09:00:00+00:00"}


def test_golden_reimport_is_noop(ctx: AppContext) -> None:
    import_path(ctx, FIXTURE)
    [run2] = import_path(ctx, FIXTURE)
    assert run2.items_new == 0
    assert run2.items_duplicate == GOLDEN_COUNT
    assert run2.items_updated == 0
