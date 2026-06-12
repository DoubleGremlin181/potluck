"""Deterministic synthetic Gmail-style mbox generator.

Same arguments -> identical bytes, on every machine, forever (stdlib
randomness only; string seeds use SHA-512 internally, immune to hash
randomization). Never put real personal data here.

Prefix stability: every message is generated from per-index RNGs, so the
first N messages of a larger corpus are byte-identical to the N-message
corpus — the P2 incremental-ingestion superset tests depend on this.

Shape mirrors real Gmail Takeout mbox: ``From `` envelope lines with an
asctime-style date, X-Gmail-Labels, multipart/alternative with
quoted-printable HTML, base64 attachments, reply chains carrying full
root-first References.
"""

import base64
import quopri
import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO

from potluck.testing.generators import WORDS

_DOMAIN_PRIMARY = "potluck.test"
_DOMAIN_SECONDARY = "example.com"
_SENDERS = tuple(f"{w}@{_DOMAIN_PRIMARY}" for w in WORDS[:10]) + (
    f"{WORDS[10]}@{_DOMAIN_SECONDARY}",
    f"{WORDS[11]}@{_DOMAIN_SECONDARY}",
)
_LABEL_SETS = (
    ("Inbox", "Unread"),
    ("Inbox", "Category Updates"),
    ("Inbox", "Category Promotions", "Unread"),
    ("Sent",),
    ("Archived", "Important"),
    (),
)
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")  # fmt: skip

_START = datetime(2021, 1, 1, 8, 0, 0, tzinfo=UTC)
_PARENT_WINDOW = 20
_MAX_REFERENCES = 10

# Default shape ratios (see synthetic_mbox_messages).
_REPLY_RATIO = 0.35
_HTML_ONLY_RATIO = 0.10
_ATTACHMENT_RATIO = 0.10
_ALTERNATIVE_RATIO = 0.30
_MISSING_MSGID_RATIO = 0.04
_DUP_MSGID_RATIO = 0.02


def _rng(seed: int, index: int, salt: str) -> random.Random:
    """Independent deterministic RNG per (message, concern).

    Separate salts keep concerns independently recomputable: ancestor walks
    re-derive reply/parent decisions without generating whole messages.
    """
    return random.Random(f"{seed}:{index}:{salt}")


def _msgid(seed: int, index: int) -> str:
    return f"<synth-{seed}-{index:06d}@{_DOMAIN_PRIMARY}>"


def _is_missing_msgid(seed: int, index: int) -> bool:
    return _rng(seed, index, "missing").random() < _MISSING_MSGID_RATIO


def _is_dup_msgid(seed: int, index: int) -> bool:
    return index > 0 and _rng(seed, index, "dup").random() < _DUP_MSGID_RATIO


def _parent(seed: int, index: int) -> int | None:
    """Reply parent for *index*, or None. O(1); avoids missing-msgid parents."""
    rng = _rng(seed, index, "thread")
    if index == 0 or rng.random() >= _REPLY_RATIO:
        return None
    parent = rng.randrange(max(0, index - _PARENT_WINDOW), index)
    for _ in range(3):
        if not _is_missing_msgid(seed, parent):
            break
        parent = rng.randrange(max(0, index - _PARENT_WINDOW), index)
    return parent


def _ancestors(seed: int, index: int) -> list[int]:
    """Reply chain above *index*, root-first, capped at _MAX_REFERENCES."""
    chain: list[int] = []
    current = index
    for _ in range(_MAX_REFERENCES):
        parent = _parent(seed, current)
        if parent is None:
            break
        chain.append(parent)
        current = parent
    chain.reverse()
    return chain


def _subject(seed: int, index: int) -> str:
    rng = _rng(seed, index, "subject")
    return " ".join(rng.choices(WORDS, k=3)).title()


def _timestamp(index: int) -> datetime:
    return _START + timedelta(minutes=index * 7)


def _envelope_date(dt: datetime) -> str:
    """Gmail envelope format: ``Fri Dec 12 06:57:49 +0000 2025``."""
    return (
        f"{_DAYS[dt.weekday()]} {_MONTHS[dt.month - 1]} {dt.day:02d} {dt:%H:%M:%S} +0000 {dt.year}"
    )


def _rfc2822_date(dt: datetime) -> str:
    return (
        f"{_DAYS[dt.weekday()]}, {dt.day:02d} {_MONTHS[dt.month - 1]} {dt.year} {dt:%H:%M:%S} +0000"
    )


def _body_text(seed: int, index: int, body_kb: int) -> str:
    rng = _rng(seed, index, "body")
    paragraphs = [
        " ".join(rng.choices(WORDS, k=rng.randint(6, 14))).capitalize() + "."
        for _ in range(rng.randint(1, 4))
    ]
    text = "\n\n".join(paragraphs)
    if body_kb > 0:
        filler_unit = " ".join(rng.choices(WORDS, k=100)) + "\n"
        repeats = (body_kb * 1024) // len(filler_unit) + 1
        text = text + "\n\n" + filler_unit * repeats
    return text


def _html_body(text: str) -> str:
    paragraphs = "".join(f"<p>{p}</p>\n" for p in text.split("\n\n"))
    return f"<html><body>\n{paragraphs}</body></html>"


def _qp(text: str) -> bytes:
    return quopri.encodestring(text.encode("utf-8"))


def synthetic_mbox_messages(
    count: int,
    seed: int = 42,
    *,
    body_kb: int = 0,
) -> Iterator[bytes]:
    """Yield ``count`` complete mbox entries (envelope line + message + blank
    separator) as bytes.

    Fixed shape mix: ~35% replies (with root-first References), ~10%
    HTML-only, ~10% with a base64 attachment, ~30% multipart/alternative,
    ~4% missing Message-ID, ~2% duplicating the previous Message-ID.
    ``body_kb`` inflates each body by roughly that many KB (bench corpora).
    """
    for i in range(count):
        yield _build_entry(seed, i, body_kb)


def _build_entry(seed: int, i: int, body_kb: int) -> bytes:
    rng = _rng(seed, i, "shape")
    sender = rng.choice(_SENDERS)
    to = rng.sample(_SENDERS, k=rng.randint(1, 2))
    cc = rng.sample(_SENDERS, k=1) if rng.random() < 0.2 else []
    labels = rng.choice(_LABEL_SETS)
    shape_roll = rng.random()

    dt = _timestamp(i)
    parent = _parent(seed, i)
    subject = _subject(seed, parent if parent is not None else i)
    if parent is not None:
        subject = f"Re: {subject}"

    headers: list[str] = [
        f"Date: {_rfc2822_date(dt)}",
        f"From: {sender.split('@')[0].title()} <{sender}>",
        f"To: {', '.join(to)}",
    ]
    if cc:
        headers.append(f"Cc: {cc[0]}")
    headers.append(f"Subject: {subject}")
    if not _is_missing_msgid(seed, i):
        msgid = _msgid(seed, i - 1) if _is_dup_msgid(seed, i) else _msgid(seed, i)
        headers.append(f"Message-ID: {msgid}")
    if parent is not None:
        chain = _ancestors(seed, i)
        headers.append(f"In-Reply-To: {_msgid(seed, parent)}")
        headers.append(f"References: {' '.join(_msgid(seed, a) for a in chain)}")
    if labels:
        headers.append(f"X-Gmail-Labels: {','.join(labels)}")
    headers.append("MIME-Version: 1.0")

    text = _body_text(seed, i, body_kb)
    body = _render_body(seed, i, headers, text, shape_roll, rng)

    envelope = f"From {sender} {_envelope_date(dt)}\n"
    head = "\n".join(headers) + "\n\n"
    return envelope.encode("ascii") + head.encode("utf-8") + body + b"\n\n"


def _render_body(
    seed: int,
    i: int,
    headers: list[str],
    text: str,
    shape_roll: float,
    rng: random.Random,
) -> bytes:
    """Append Content-Type headers to *headers* and return the body bytes."""
    if shape_roll < _HTML_ONLY_RATIO:
        headers.append('Content-Type: text/html; charset="UTF-8"')
        headers.append("Content-Transfer-Encoding: quoted-printable")
        return _qp(_html_body(text))

    if shape_roll < _HTML_ONLY_RATIO + _ATTACHMENT_RATIO:
        boundary = f"=-=synth-{seed}-{i}=-="
        payload = rng.randbytes(rng.randint(64, 512))
        encoded = base64.encodebytes(payload).decode("ascii")
        headers.append(f'Content-Type: multipart/mixed; boundary="{boundary}"')
        parts = (
            f"--{boundary}\n"
            'Content-Type: text/plain; charset="UTF-8"\n'
            "\n"
            f"{text}\n"
            f"--{boundary}\n"
            "Content-Type: application/octet-stream\n"
            f'Content-Disposition: attachment; filename="file-{i:06d}.bin"\n'
            "Content-Transfer-Encoding: base64\n"
            "\n"
            f"{encoded}"
            f"--{boundary}--\n"
        )
        return parts.encode("utf-8")

    if shape_roll < _HTML_ONLY_RATIO + _ATTACHMENT_RATIO + _ALTERNATIVE_RATIO:
        boundary = f"=-=synth-{seed}-{i}=-="
        headers.append(f'Content-Type: multipart/alternative; boundary="{boundary}"')
        html_qp = _qp(_html_body(text)).decode("ascii")
        parts = (
            f"--{boundary}\n"
            'Content-Type: text/plain; charset="UTF-8"\n'
            "\n"
            f"{text}\n"
            f"--{boundary}\n"
            'Content-Type: text/html; charset="UTF-8"\n'
            "Content-Transfer-Encoding: quoted-printable\n"
            "\n"
            f"{html_qp}\n"
            f"--{boundary}--\n"
        )
        return parts.encode("utf-8")

    headers.append('Content-Type: text/plain; charset="UTF-8"')
    return text.encode("utf-8")


def write_mbox(
    dest: Path,
    count: int,
    seed: int = 42,
    *,
    body_kb: int = 0,
) -> Path:
    """Stream ``count`` synthetic messages to *dest* — never builds the corpus
    in memory (multi-GB bench corpora are generated this way)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    out: IO[bytes]
    with dest.open("wb") as out:
        for entry in synthetic_mbox_messages(count, seed, body_kb=body_kb):
            out.write(entry)
    return dest
