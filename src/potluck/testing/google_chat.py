"""Deterministic synthetic Google Chat Takeout generator.

Ships inside ``potluck.testing`` so tests, committed fixtures, and bench
scenarios share one deterministic source. Same arguments → identical bytes on
every machine, forever. Never put real personal data here — every email lives
under ``@potluck.test``, names are fixture names, message text comes from the
shared WORDS list.

The member set mirrors the real 2025-12 export (structure verified against a
real Takeout part, shape only): ``Takeout/Google Chat/Groups/<group>/`` pairs
of ``group_info.json`` + ``messages.json`` where ``<group>`` is ``DM <id>``
or ``Space <id>``; a ``Users/User <id>/`` dir with ``user_info.json`` (the
export owner — the parser's DM-naming sidecar) and ``unsentmessages.json``
(a detection-precision decoy); and a ``File-*`` attachment blob the parser
must never read. Key names, key ORDER, and the ``created_date`` rendering
(``"Friday, March 17, 2023 at 9:00:00 AM UTC"`` — full English names,
non-padded day/hour, a NARROW NO-BREAK SPACE before the meridiem, always
UTC) match the real export exactly. Two groups
are populated (one DM, one named Space — the #147 acceptance pair); a second
DM is empty (three of the real export's ten DMs are), pinning the
empty-group silence.

Record shapes are modular rules of the record index ``i`` (not RNG draws),
so expected parser outcomes have exact closed forms. Per record ``i`` of a
populated group (first rule wins):

- ``i % 17 == 16`` (i > 0) → verbatim copy of record ``i-1`` — same
  message_id, same bytes. Real exports mint globally unique message ids;
  this exercises the parser's defensive ``#N`` identity suffixes
  (:func:`expected_duplicate_suffix_count`).
- ``i % 20 == 3``  → membership/system stub: creator + created_date + ids
  but neither ``text`` nor ``attached_files`` — the non-content shape the
  parser must skip (:func:`expected_message_count`).
- ``i % 12 == 9``  → attachment record (``attached_files`` with the real
  ``export_name``/``original_name`` pair, no text;
  :func:`expected_media_reference_count`).
- ``i % 7 == 2``   → multi-line text (embedded newlines).
- otherwise        → plain WORDS-derived text; emoji at ``i % 10 == 4``.

Timestamps (:func:`message_ts`): 3 minutes apart from a fixed base with a
17-second jitter, rendered through hardcoded English name tables (never
``strftime`` — its names are locale-dependent).

Regenerate the golden fixture::

    python -c "
    from pathlib import Path
    from potluck.testing.google_chat import write_google_chat_takeout
    write_google_chat_takeout(Path('tests/fixtures/google_chat'), 40, seed=7, fmt='dir')
    "
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from potluck.testing.archives import write_archive
from potluck.testing.generators import WORDS

_BASE_TS = datetime(2023, 3, 17, 9, 0, 0, tzinfo=UTC)

_ROOT = "Takeout/Google Chat"
_DM_ID = "synthdm01AAAE"
_DM_EMPTY_ID = "synthdm02AAAE"
_SPACE_ID = "AAAAsynthsp1"
_SPACE_NAME = "Synthetic Fixture Crew"
_USER_DIR = "User 000000000000000000042"

_OWNER = ("Ada Example", "ada@potluck.test")
_MEMBERS_DM = (_OWNER, ("Bo Sample", "bo@potluck.test"))
_MEMBERS_SPACE = (*_MEMBERS_DM, ("Cy Test", "cy@potluck.test"))

_EMOJI = ("🎉", "🚀", "🥘", "✨")

# English name tables: the real export renders these names regardless of the
# machine locale, and so must the generator (strftime %A/%B do not).
_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _shape(i: int) -> str:
    """The modular shape rule for record *i* (module docstring)."""
    if i > 0 and i % 17 == 16:
        return "dup"
    if i % 20 == 3:
        return "membership"
    if i % 12 == 9:
        return "attached"
    if i % 7 == 2:
        return "multiline"
    return "plain"


def _effective_shape(i: int) -> str:
    """The shape a record renders as (a dup copies its predecessor)."""
    return _shape(i - 1) if _shape(i) == "dup" else _shape(i)


def expected_message_count(count: int) -> int:
    """Messages the parser yields for ONE populated group of *count* records
    (membership stubs are skipped; duplicates import via ``#N`` suffixes)."""
    return sum(1 for i in range(count) if _effective_shape(i) != "membership")


def expected_media_reference_count(count: int) -> int:
    """files-row references (attachment records) for one populated group."""
    return sum(1 for i in range(count) if _effective_shape(i) == "attached")


def expected_duplicate_suffix_count(count: int) -> int:
    """Records that import with a ``#N`` external-id suffix (verbatim dups
    of a non-membership record) for one populated group."""
    return sum(
        1 for i in range(count) if _shape(i) == "dup" and _effective_shape(i) != "membership"
    )


def message_ts(i: int) -> datetime:
    """The instant of record *i*: 3 minutes apart with second jitter."""
    return _BASE_TS + timedelta(minutes=3 * i, seconds=(i * 17) % 60)


def _created_date(dt: datetime) -> str:
    """Render *dt* exactly as the real export does (module docstring)."""
    hour = dt.hour % 12 or 12
    meridiem = "AM" if dt.hour < 12 else "PM"
    return (
        f"{_DAYS[dt.weekday()]}, {_MONTHS[dt.month - 1]} {dt.day}, {dt.year} "
        f"at {hour}:{dt.minute:02d}:{dt.second:02d}\u202f{meridiem} UTC"
    )


def _words(salt: int, i: int, offset: int, k: int) -> str:
    return " ".join(WORDS[(salt + i * 7 + (offset + j) * 3) % len(WORDS)] for j in range(k))


def _member_json(name: str, email: str) -> dict[str, object]:
    return {"name": name, "email": email, "user_type": "Human"}


def _record(i: int, gid: str, members: tuple[tuple[str, str], ...], salt: int) -> dict[str, object]:
    """The exported record for logical index *i* (never called for dups),
    field names and order exactly as the real 2025-12 export."""
    name, email = members[(salt + i) % len(members)]
    record: dict[str, object] = {
        "creator": _member_json(name, email),
        "created_date": _created_date(message_ts(i)),
    }
    shape = _shape(i)
    if shape == "attached":
        stem = f"synthetic-{i}"
        record["attached_files"] = [
            {"export_name": f"File-{stem}.png", "original_name": f"{stem}.png"}
        ]
    elif shape != "membership":
        text = _words(salt, i, 0, 4 + i % 5)
        if i % 10 == 4:
            text += " " + _EMOJI[i % len(_EMOJI)]
        if shape == "multiline":
            text += "\n" + _words(salt, i, 50, 3) + "\n\n" + _words(salt, i, 80, 4)
        record["text"] = text
    record["topic_id"] = f"syntopic-{i:04d}"
    record["message_id"] = f"{gid}/syntopic-{i:04d}/synmsg-{i:04d}"
    return record


def _dump(doc: dict[str, object]) -> bytes:
    return json.dumps(doc, ensure_ascii=False, indent=2).encode()


def _messages_bytes(count: int, gid: str, members: tuple[tuple[str, str], ...], salt: int) -> bytes:
    records = [
        _record(i - 1 if _shape(i) == "dup" else i, gid, members, salt) for i in range(count)
    ]
    return _dump({"messages": records})


def _group_info_bytes(members: tuple[tuple[str, str], ...], name: str | None = None) -> bytes:
    doc: dict[str, object] = {}
    if name is not None:
        doc["name"] = name
    doc["members"] = [_member_json(n, e) for n, e in members]
    return _dump(doc)


def google_chat_members(count: int, seed: int = 42) -> dict[str, bytes]:
    """The member set of one synthetic Takeout ({posix_name: content})."""
    salt = seed * 1009
    groups = f"{_ROOT}/Groups"
    user_info: dict[str, object] = {
        "user": _member_json(*_OWNER),
        "membership_info": [
            {"group_id": f"DM {_DM_ID}", "membership_state": "MEMBER_JOINED"},
            {"group_id": f"Space {_SPACE_ID}", "membership_state": "MEMBER_JOINED"},
        ],
    }
    return {
        f"{groups}/DM {_DM_ID}/group_info.json": _group_info_bytes(_MEMBERS_DM),
        f"{groups}/DM {_DM_ID}/messages.json": _messages_bytes(count, _DM_ID, _MEMBERS_DM, salt),
        # An attachment blob beside its messages.json — never read at parse
        # time (metadata only until P6 pixel ingestion).
        f"{groups}/DM {_DM_ID}/File-synthetic-9.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 8,
        f"{groups}/DM {_DM_EMPTY_ID}/group_info.json": _group_info_bytes(_MEMBERS_DM),
        f"{groups}/DM {_DM_EMPTY_ID}/messages.json": _dump({"messages": []}),
        f"{groups}/Space {_SPACE_ID}/group_info.json": _group_info_bytes(
            _MEMBERS_SPACE, name=_SPACE_NAME
        ),
        f"{groups}/Space {_SPACE_ID}/messages.json": _messages_bytes(
            count, _SPACE_ID, _MEMBERS_SPACE, salt + 101
        ),
        f"{_ROOT}/Users/{_USER_DIR}/user_info.json": _dump(user_info),
        f"{_ROOT}/Users/{_USER_DIR}/unsentmessages.json": _dump({"unsent_messages": []}),
    }


def write_google_chat_takeout(
    dest_dir: Path,
    count: int,
    seed: int = 42,
    *,
    fmt: Literal["zip", "tgz", "dir"] = "zip",
) -> Path:
    """Materialise a synthetic Google Chat Takeout archive in *dest_dir*.

    Returns the archive path (or the directory root for ``fmt="dir"``).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    members = google_chat_members(count, seed)
    if fmt == "dir":
        dest = dest_dir / "google-chat-synth-001"
        write_archive(dest, members, "dir")
        return dest
    ext = "zip" if fmt == "zip" else "tgz"
    dest = dest_dir / f"google-chat-synth-001.{ext}"
    write_archive(dest, members, fmt)
    return dest
