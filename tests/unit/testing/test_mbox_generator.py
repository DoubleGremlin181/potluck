"""Synthetic mbox generator (#122): deterministic, prefix-stable, PII-safe."""

import email
import email.policy
from io import BytesIO
from pathlib import Path

from potluck.testing.mbox import synthetic_mbox_messages, write_mbox

_ALLOWED_DOMAINS = ("potluck.test", "example.com")


def _parse_all(count: int, seed: int) -> list[email.message.EmailMessage]:
    parsed = []
    for raw in synthetic_mbox_messages(count, seed=seed):
        body = raw.split(b"\n", 1)[1]  # drop the From_ envelope line
        msg = email.message_from_bytes(body, policy=email.policy.default)
        assert isinstance(msg, email.message.EmailMessage)
        parsed.append(msg)
    return parsed


def test_same_seed_same_bytes() -> None:
    a = list(synthetic_mbox_messages(30, seed=9))
    b = list(synthetic_mbox_messages(30, seed=9))
    assert a == b


def test_different_seed_different_bytes() -> None:
    a = b"".join(synthetic_mbox_messages(10, seed=1))
    b = b"".join(synthetic_mbox_messages(10, seed=2))
    assert a != b


def test_prefix_stable() -> None:
    """First N messages of a larger corpus are byte-identical to the N corpus.

    Required by the P2 incremental-ingestion superset test (#126): a newer,
    larger Takeout must contain the old corpus verbatim.
    """
    small = list(synthetic_mbox_messages(20, seed=9))
    large = list(synthetic_mbox_messages(35, seed=9))
    assert large[:20] == small


def test_every_message_starts_with_envelope() -> None:
    for raw in synthetic_mbox_messages(20, seed=9):
        assert raw.startswith(b"From ")


def test_only_allowed_email_domains() -> None:
    for msg in _parse_all(60, seed=9):
        for header in ("From", "To", "Cc"):
            for addr in msg.get_all(header, []):
                text = str(addr)
                if "@" not in text:
                    continue
                assert any(d in text for d in _ALLOWED_DOMAINS), text


def test_corpus_has_replies_with_references() -> None:
    msgs = _parse_all(60, seed=9)
    with_refs = [m for m in msgs if m["References"]]
    assert with_refs, "expected some replies in a 60-message corpus"
    ids = {m["Message-ID"] for m in msgs if m["Message-ID"]}
    # References point at earlier messages in the same corpus.
    referenced = with_refs[0]["References"].split()
    assert all(r in ids for r in referenced)


def test_corpus_has_labels() -> None:
    msgs = _parse_all(40, seed=9)
    assert any(m["X-Gmail-Labels"] for m in msgs)


def test_corpus_has_html_only_messages() -> None:
    msgs = _parse_all(80, seed=9)
    html_only = [m for m in msgs if m.get_content_type() == "text/html"]
    assert html_only


def test_corpus_has_attachments() -> None:
    msgs = _parse_all(80, seed=9)
    assert any(m.get_content_type() == "multipart/mixed" for m in msgs)


def test_missing_msgid_ratio() -> None:
    msgs = _parse_all(100, seed=9)
    missing = [m for m in msgs if not m["Message-ID"]]
    assert 0 < len(missing) <= 10


def test_duplicate_msgid_pair_present() -> None:
    msgs = _parse_all(200, seed=9)
    ids = [m["Message-ID"] for m in msgs if m["Message-ID"]]
    assert len(ids) != len(set(ids)), "expected at least one duplicate Message-ID"


def test_write_mbox_streams_to_disk(tmp_path: Path) -> None:
    out = write_mbox(tmp_path / "test.mbox", 25, seed=9)
    assert out == tmp_path / "test.mbox"
    data = out.read_bytes()
    assert data.count(b"\nFrom ") + data.startswith(b"From ") == 25


def test_write_mbox_matches_generator(tmp_path: Path) -> None:
    out = write_mbox(tmp_path / "test.mbox", 10, seed=9)
    assert out.read_bytes() == b"".join(synthetic_mbox_messages(10, seed=9))


def test_body_kb_inflates_messages(tmp_path: Path) -> None:
    small = write_mbox(tmp_path / "small.mbox", 5, seed=9)
    big = write_mbox(tmp_path / "big.mbox", 5, seed=9, body_kb=64)
    assert big.stat().st_size > small.stat().st_size + 5 * 60_000


def test_round_trips_through_stdlib_mbox_split() -> None:
    """The generated corpus is well-formed enough for the real parser chain."""
    from potluck.ingest.mbox import iter_mbox_messages

    raw = b"".join(synthetic_mbox_messages(30, seed=9))
    assert len(list(iter_mbox_messages(BytesIO(raw)))) == 30
