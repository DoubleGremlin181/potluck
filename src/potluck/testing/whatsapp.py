"""Deterministic synthetic WhatsApp chat-export generator.

Ships inside ``potluck.testing`` so tests, committed fixtures, and bench
scenarios share one deterministic source. Same arguments → identical output
on every machine, forever. Never put real personal data here — senders are
fixture names (never phone-number-shaped strings; the PII guard rejects
those), message text comes from the shared WORDS list.

Message shapes are modular rules of the message index (not RNG draws), so
expected parser outcomes have exact closed forms — see
:func:`expected_message_count` / :func:`expected_media_reference_count`.
Per logical message ``i`` (first matching rule wins):

- ``i % 50 == 48`` (i > 0) → verbatim copy of message ``i-1`` (same
  timestamp, same text: exercises the parser's ``#N`` identity suffixes)
- ``i % 20 == 3``  → system line (skipped by the parser)
- ``i % 12 == 9``  → media with filename (a ``files`` reference)
- ``i % 12 == 5``  → bare media placeholder (no filename)
- ``i % 7 == 2``   → 3-line message (continuation-line concatenation)
- otherwise        → plain text; emoji at ``i % 10 == 4``, RTL text at
  ``i % 25 == 6``

Locales render the same logical messages in different export dialects:

- ``us``  → Android month-first 12h (``3/17/23, 9:00 AM - ``)
- ``eu``  → Android day-first 24h (``17/03/2023, 09:00 - ``)
- ``ios`` → iPhone bracketed with seconds, NARROW NO-BREAK SPACE before
  AM/PM, LRM-marked media/system lines, ``_chat.txt`` naming

The base date (2023-03-17) makes day/month order decisive from the first
line in every locale.

Regenerate the golden fixture::

    python -c "
    from pathlib import Path
    from potluck.testing.whatsapp import write_whatsapp_export
    write_whatsapp_export(Path('tests/fixtures/whatsapp'), 40, seed=7,
                          locales=('us', 'eu', 'ios'), fmt='dir')
    "
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from potluck.testing.archives import write_archive
from potluck.testing.generators import WORDS

Locale = Literal["us", "eu", "ios"]

_BASE_TS = datetime(2023, 3, 17, 9, 0, 0, tzinfo=UTC)

_CHAT_NAMES: dict[str, str] = {"us": "Ada Example", "eu": "Dana Muster", "ios": "Rina Sample"}
_SENDERS = ("Ada Example", "Bo Sample", "Cy Test", "Dee Fixture")
_EMOJI = ("🎉", "🚀", "🥘", "✨")
_RTL = ("مرحبا بالجميع", "שלום לכולם")


def _shape(i: int) -> str:
    """The modular shape rule for logical message *i* (module docstring)."""
    if i > 0 and i % 50 == 48:
        return "dup"
    if i % 20 == 3:
        return "system"
    if i % 12 == 9:
        return "attached"
    if i % 12 == 5:
        return "omitted"
    if i % 7 == 2:
        return "multiline"
    return "plain"


def _effective_shape(i: int) -> str:
    """The shape a message renders as (a dup copies its predecessor)."""
    return _shape(i - 1) if _shape(i) == "dup" else _shape(i)


def expected_message_count(count: int) -> int:
    """Messages the parser yields for one generated chat of *count* lines."""
    return sum(1 for i in range(count) if _effective_shape(i) != "system")


def expected_media_reference_count(count: int) -> int:
    """files-row references (media with filenames) for one generated chat."""
    return sum(1 for i in range(count) if _effective_shape(i) == "attached")


def _ts(i: int) -> datetime:
    """3 minutes apart, with second jitter only the ios dialect renders."""
    return _BASE_TS + timedelta(minutes=3 * i, seconds=(i * 17) % 60)


def _prefix(dt: datetime, locale: Locale) -> str:
    if locale == "eu":
        return f"{dt.day:02d}/{dt.month:02d}/{dt.year}, {dt.hour:02d}:{dt.minute:02d} - "
    hour = dt.hour % 12 or 12
    meridiem = "AM" if dt.hour < 12 else "PM"
    if locale == "us":
        return f"{dt.month}/{dt.day}/{dt.year % 100:02d}, {hour}:{dt.minute:02d} {meridiem} - "
    return (
        f"[{dt.month}/{dt.day}/{dt.year % 100:02d}, "
        f"{hour}:{dt.minute:02d}:{dt.second:02d}\u202f{meridiem}] "
    )


def _words(salt: int, i: int, offset: int, k: int) -> str:
    return " ".join(WORDS[(salt + i * 7 + (offset + j) * 3) % len(WORDS)] for j in range(k))


def _message_lines(i: int, locale: Locale, salt: int) -> list[str]:
    """The physical export lines for logical message *i* (never a dup)."""
    dt = _ts(i)
    pre = _prefix(dt, locale)
    sender = _SENDERS[(salt + i) % len(_SENDERS)]
    shape = _shape(i)

    if shape == "system":
        variant = (i // 20) % 3
        if locale == "ios" and variant == 0:
            # iOS pins the encryption notice to a participant, LRM-marked.
            return [
                f"{pre}{sender}: \u200eMessages and calls are end-to-end encrypted. "
                "No one outside of this chat, not even WhatsApp, can read or listen to them."
            ]
        texts = (
            "Messages and calls are end-to-end encrypted. Tap to learn more.",
            f'{sender} created group "Synthetic Fixture Crew"',
            f"{sender} joined using this group's invite link",
        )
        mark = "\u200e" if locale == "ios" else ""
        return [f"{mark}{pre}{texts[variant]}"]

    if shape == "attached":
        if locale == "ios":
            return [f"{pre}{sender}: \u200e<attached: {i:08d}-PHOTO-{dt:%Y-%m-%d-%H-%M-%S}.jpg>"]
        return [f"{pre}{sender}: IMG-{dt:%Y%m%d}-WA{i % 10000:04d}.jpg (file attached)"]

    if shape == "omitted":
        if locale == "ios":
            return [f"{pre}{sender}: \u200eimage omitted"]
        return [f"{pre}{sender}: <Media omitted>"]

    text = _words(salt, i, 0, 4 + i % 5)
    if i % 10 == 4:
        text += " " + _EMOJI[i % len(_EMOJI)]
    if i % 25 == 6:
        text += " " + _RTL[i % len(_RTL)]
    if shape == "multiline":
        return [f"{pre}{sender}: {text}", _words(salt, i, 50, 3), _words(salt, i, 80, 4)]
    return [f"{pre}{sender}: {text}"]


def synthetic_chat_lines(
    count: int, seed: int = 42, *, locale: Locale = "us", chat_ordinal: int = 0
) -> Iterator[str]:
    """Yield the physical lines of one chat export with *count* logical
    messages. *chat_ordinal* differentiates content across the chats of one
    archive without disturbing the shared shape rules."""
    salt = seed * 1009 + chat_ordinal * 101
    for i in range(count):
        index = i - 1 if _shape(i) == "dup" else i
        yield from _message_lines(index, locale, salt)


def write_whatsapp_export(
    dest_dir: Path,
    count: int,
    seed: int = 42,
    *,
    locales: tuple[Locale, ...] = ("us", "eu"),
    chats_per_locale: int = 1,
    fmt: Literal["zip", "tgz", "dir"] = "zip",
) -> Path:
    """Materialise a synthetic WhatsApp export archive in *dest_dir*.

    One chat per (locale, ordinal), *count* logical messages each. Android
    locales land as ``WhatsApp Chat with <name>.txt``; ios chats land as
    ``WhatsApp Chat - <name>/_chat.txt`` beside a tiny decoy media member
    (the parser must never read it). A root ``notes.txt`` decoy pins
    detection precision. Returns the archive path (or the directory root
    for ``fmt="dir"``).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    members: dict[str, bytes] = {}
    ordinal = 0
    for locale in locales:
        for chat in range(chats_per_locale):
            name = _CHAT_NAMES[locale] + (f" {chat + 1}" if chat else "")
            body = (
                "\n".join(synthetic_chat_lines(count, seed, locale=locale, chat_ordinal=ordinal))
                + "\n"
            )
            if locale == "ios":
                folder = f"WhatsApp Chat - {name}"
                members[f"{folder}/_chat.txt"] = body.encode()
                members[f"{folder}/00000000-PHOTO-2023-03-17-09-00-00.jpg"] = (
                    b"\xff\xd8\xff" + b"\x00" * 8
                )
            else:
                members[f"WhatsApp Chat with {name}.txt"] = body.encode()
            ordinal += 1
    members["notes.txt"] = b"decoy: a plain text file no chat parser should read\n"

    if fmt == "dir":
        dest = dest_dir / "whatsapp-synth-001"
        write_archive(dest, members, "dir")
        return dest
    ext = "zip" if fmt == "zip" else "tgz"
    dest = dest_dir / f"whatsapp-synth-001.{ext}"
    write_archive(dest, members, fmt)
    return dest
