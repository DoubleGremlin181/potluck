"""Streaming mbox splitter + MIME email parser (#122).

Message fixtures are small literal byte strings modeled on the real Gmail
Takeout mbox shape (From_ envelope lines, X-Gmail-Labels, multipart/alternative
with quoted-printable HTML) — content is synthetic.
"""

import hashlib
from datetime import UTC, datetime
from io import BytesIO

from potluck.ingest.mbox import iter_mbox_messages, normalize_msgid, parse_email

# ---------------------------------------------------------------------------
# iter_mbox_messages
# ---------------------------------------------------------------------------

TWO_MESSAGE_MBOX = (
    b"From alice@potluck.test Fri Dec 12 06:57:49 +0000 2025\n"
    b"Message-ID: <one@potluck.test>\n"
    b"Subject: first\n"
    b"\n"
    b"body one\n"
    b"\n"
    b"From bob@potluck.test Fri Dec 12 07:00:00 +0000 2025\n"
    b"Message-ID: <two@potluck.test>\n"
    b"Subject: second\n"
    b"\n"
    b"body two\n"
)


def test_iter_splits_on_envelope_lines() -> None:
    messages = list(iter_mbox_messages(BytesIO(TWO_MESSAGE_MBOX)))
    assert len(messages) == 2
    assert messages[0].startswith(b"Message-ID: <one@potluck.test>")
    assert b"body one" in messages[0]
    assert messages[1].startswith(b"Message-ID: <two@potluck.test>")
    assert b"body two" in messages[1]


def test_iter_excludes_envelope_from_line() -> None:
    messages = list(iter_mbox_messages(BytesIO(TWO_MESSAGE_MBOX)))
    for msg in messages:
        assert not msg.startswith(b"From ")


def test_iter_quoted_from_stays_in_message() -> None:
    raw = (
        b"From alice@potluck.test Fri Dec 12 06:57:49 +0000 2025\n"
        b"Subject: quoting\n"
        b"\n"
        b"line one\n"
        b">From the archives\n"
        b"line three\n"
    )
    messages = list(iter_mbox_messages(BytesIO(raw)))
    assert len(messages) == 1
    assert b">From the archives" in messages[0]


def test_iter_final_message_without_trailing_newline() -> None:
    raw = (
        b"From alice@potluck.test Fri Dec 12 06:57:49 +0000 2025\n"
        b"Subject: last\n"
        b"\n"
        b"no trailing newline"
    )
    messages = list(iter_mbox_messages(BytesIO(raw)))
    assert len(messages) == 1
    assert messages[0].endswith(b"no trailing newline")


def test_iter_empty_stream() -> None:
    assert list(iter_mbox_messages(BytesIO(b""))) == []


def test_iter_ignores_leading_garbage_before_first_envelope() -> None:
    raw = b"\n\nFrom alice@potluck.test Fri Dec 12 06:57:49 +0000 2025\nSubject: x\n\nbody\n"
    messages = list(iter_mbox_messages(BytesIO(raw)))
    assert len(messages) == 1


# ---------------------------------------------------------------------------
# normalize_msgid
# ---------------------------------------------------------------------------


def test_normalize_msgid_strips_brackets_and_whitespace() -> None:
    assert normalize_msgid(" <abc@potluck.test> ") == "abc@potluck.test"
    assert normalize_msgid("abc@potluck.test") == "abc@potluck.test"


def test_normalize_msgid_empty_inputs() -> None:
    assert normalize_msgid(None) is None
    assert normalize_msgid("") is None
    assert normalize_msgid(" <> ") is None


# ---------------------------------------------------------------------------
# parse_email — headers
# ---------------------------------------------------------------------------


def _msg(*lines: bytes) -> bytes:
    return b"\n".join(lines)


BASIC = _msg(
    b"Message-ID: <one@potluck.test>",
    b"From: Alice Example <ALICE@Potluck.test>",
    b"To: Bob <bob@potluck.test>, carol@example.com",
    b"Cc: dave@potluck.test",
    b"Subject: garden notes",
    b"Date: Fri, 12 Dec 2025 06:57:49 +0000",
    b"X-Gmail-Labels: Inbox,Category Updates,Unread",
    b"Content-Type: text/plain; charset=UTF-8",
    b"",
    b"plain body here",
)


def test_parse_basic_headers() -> None:
    parsed = parse_email(BASIC)
    assert parsed.message_id == "one@potluck.test"
    assert parsed.from_addr == "alice@potluck.test"
    assert parsed.to_addrs == ("bob@potluck.test", "carol@example.com")
    assert parsed.cc_addrs == ("dave@potluck.test",)
    assert parsed.subject == "garden notes"
    assert parsed.date == datetime(2025, 12, 12, 6, 57, 49, tzinfo=UTC)
    assert parsed.labels == ("Inbox", "Category Updates", "Unread")
    assert parsed.text.strip() == "plain body here"
    assert parsed.attachments == ()


def test_parse_rfc2047_subject() -> None:
    parsed = parse_email(_msg(b"Subject: =?utf-8?q?caf=C3=A9_notes?=", b"", b"x"))
    assert parsed.subject == "café notes"


def test_parse_references_and_in_reply_to() -> None:
    parsed = parse_email(
        _msg(
            b"Message-ID: <c@potluck.test>",
            b"In-Reply-To: <b@potluck.test>",
            b"References: <a@potluck.test> <b@potluck.test>",
            b"",
            b"x",
        )
    )
    assert parsed.in_reply_to == "b@potluck.test"
    assert parsed.references == ("a@potluck.test", "b@potluck.test")


def test_parse_missing_message_id() -> None:
    parsed = parse_email(_msg(b"Subject: nope", b"", b"x"))
    assert parsed.message_id is None


def test_parse_bad_date_yields_none() -> None:
    parsed = parse_email(_msg(b"Date: not a date at all", b"", b"x"))
    assert parsed.date is None


def test_parse_no_labels_header() -> None:
    parsed = parse_email(_msg(b"Subject: x", b"", b"x"))
    assert parsed.labels == ()


# ---------------------------------------------------------------------------
# parse_email — bodies and charsets
# ---------------------------------------------------------------------------


def test_parse_base64_body() -> None:
    parsed = parse_email(
        _msg(
            b"Content-Type: text/plain; charset=UTF-8",
            b"Content-Transfer-Encoding: base64",
            b"",
            b"aGVsbG8gYmFzZTY0IGJvZHk=",
        )
    )
    assert parsed.text.strip() == "hello base64 body"


def test_parse_quoted_printable_body() -> None:
    parsed = parse_email(
        _msg(
            b"Content-Type: text/plain; charset=UTF-8",
            b"Content-Transfer-Encoding: quoted-printable",
            b"",
            b"caf=C3=A9 time",
        )
    )
    assert parsed.text.strip() == "café time"


def test_parse_latin1_body() -> None:
    parsed = parse_email(
        _msg(
            b"Content-Type: text/plain; charset=ISO-8859-1",
            b"",
            b"caf\xe9",
        )
    )
    assert parsed.text.strip() == "café"


def test_parse_unknown_charset_falls_back() -> None:
    parsed = parse_email(
        _msg(
            b"Content-Type: text/plain; charset=banana",
            b"",
            b"plain enough",
        )
    )
    assert "plain enough" in parsed.text


def test_parse_undeclared_charset_non_utf8_does_not_crash() -> None:
    parsed = parse_email(_msg(b"Content-Type: text/plain", b"", b"caf\xe9 again"))
    assert "caf" in parsed.text


def test_parse_html_only_body_extracts_text() -> None:
    parsed = parse_email(
        _msg(
            b"Content-Type: text/html; charset=UTF-8",
            b"",
            b"<html><body><p>Hello <b>world</b></p></body></html>",
        )
    )
    assert "Hello world" in parsed.text
    assert "<" not in parsed.text


def test_parse_empty_plain_falls_back_to_html() -> None:
    """Seen in real Gmail exports: an empty text/plain alternative next to a
    populated text/html — the html must win over whitespace."""
    parsed = parse_email(
        _msg(
            b'Content-Type: multipart/alternative; boundary="B"',
            b"",
            b"--B",
            b"Content-Type: text/plain; charset=UTF-8",
            b"",
            b"",
            b"--B",
            b"Content-Type: text/html; charset=UTF-8",
            b"",
            b"<p>only the html has content</p>",
            b"--B--",
        )
    )
    assert "only the html has content" in parsed.text


def test_parse_multipart_alternative_prefers_plain() -> None:
    parsed = parse_email(
        _msg(
            b'Content-Type: multipart/alternative; boundary="B"',
            b"",
            b"--B",
            b"Content-Type: text/plain; charset=UTF-8",
            b"",
            b"the plain version",
            b"--B",
            b"Content-Type: text/html; charset=UTF-8",
            b"",
            b"<p>the html version</p>",
            b"--B--",
        )
    )
    assert "the plain version" in parsed.text
    assert "html version" not in parsed.text


# ---------------------------------------------------------------------------
# parse_email — attachments
# ---------------------------------------------------------------------------

ATTACHMENT_PAYLOAD = b"attach bytes"

WITH_ATTACHMENT = _msg(
    b"Message-ID: <att@potluck.test>",
    b'Content-Type: multipart/mixed; boundary="B"',
    b"",
    b"--B",
    b"Content-Type: text/plain; charset=UTF-8",
    b"",
    b"see attached",
    b"--B",
    b"Content-Type: application/octet-stream",
    b'Content-Disposition: attachment; filename="a.bin"',
    b"Content-Transfer-Encoding: base64",
    b"",
    b"YXR0YWNoIGJ5dGVz",
    b"--B--",
)


def test_parse_attachment_metadata() -> None:
    parsed = parse_email(WITH_ATTACHMENT)
    assert len(parsed.attachments) == 1
    att = parsed.attachments[0]
    assert att.filename == "a.bin"
    assert att.mime == "application/octet-stream"
    assert att.size_bytes == len(ATTACHMENT_PAYLOAD)
    assert att.sha256 == hashlib.sha256(ATTACHMENT_PAYLOAD).hexdigest()


def test_parse_attachment_not_in_text() -> None:
    parsed = parse_email(WITH_ATTACHMENT)
    assert "see attached" in parsed.text
    assert "YXR0YWNo" not in parsed.text


def test_parse_inline_image_counts_as_attachment() -> None:
    parsed = parse_email(
        _msg(
            b'Content-Type: multipart/mixed; boundary="B"',
            b"",
            b"--B",
            b"Content-Type: text/plain",
            b"",
            b"body",
            b"--B",
            b"Content-Type: image/png",
            b"Content-Transfer-Encoding: base64",
            b"",
            b"aW1hZ2U=",
            b"--B--",
        )
    )
    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].mime == "image/png"
    assert parsed.attachments[0].filename is None


# ---------------------------------------------------------------------------
# round-trip with the synthetic generator
# ---------------------------------------------------------------------------


def test_round_trip_synthetic_corpus() -> None:
    from potluck.testing.mbox import synthetic_mbox_messages

    raw = b"".join(synthetic_mbox_messages(50, seed=11))
    parsed = [parse_email(m) for m in iter_mbox_messages(BytesIO(raw))]
    assert len(parsed) == 50
    assert all(p.from_addr for p in parsed)
    assert any(p.references for p in parsed)
    assert any(p.attachments for p in parsed)
