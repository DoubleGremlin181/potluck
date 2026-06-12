"""Ingest text cleanup (#199): junk-run truncation, invisible-char strip, size cap.

Lossy by design — the cleaned text is what gets hashed, stored, and indexed.
"""

from potluck.ingest.textclean import MAX_TEXT_CHARS, clean_text


def test_plain_text_passes_through_unchanged() -> None:
    text = "Hello world,\nthis is a normal email body with short tokens."
    assert clean_text(text) == text


def test_long_tracking_url_truncated_to_80_chars() -> None:
    url = "https://mail.example.com/track?" + "a" * 200
    out = clean_text(f"click here {url} thanks")
    tokens = out.split()
    assert tokens[0:2] == ["click", "here"]
    assert tokens[-1] == "thanks"
    long_token = tokens[2]
    assert len(long_token) == 80
    # The head of the run survives — URL host stays searchable.
    assert long_token.startswith("https://mail.example.com/track?")


def test_run_under_threshold_untouched() -> None:
    token = "x" * 119
    assert clean_text(token) == token


def test_run_at_threshold_truncated() -> None:
    assert clean_text("y" * 120) == "y" * 80


def test_base64_residue_truncated_per_run() -> None:
    blob = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=" * 10  # one unbroken ~370-char run
    out = clean_text(f"before {blob} after {blob}")
    for token in out.split():
        assert len(token) <= 80


def test_zero_width_and_invisible_chars_stripped() -> None:
    text = "he​llo wo‌rld z‍w j⁠oin b﻿om s­hy c͏gj"
    assert clean_text(text) == "hello world zw join bom shy cgj"


def test_zero_width_chars_do_not_mask_long_runs() -> None:
    # A 160-char run "broken" only by ZWSPs is still junk: strip happens
    # before run detection, so it gets truncated.
    run = ("z" * 40 + "​") * 4
    out = clean_text(run)
    assert out == "z" * 80


def test_size_cap_applies() -> None:
    text = "word " * (MAX_TEXT_CHARS // 4)
    out = clean_text(text)
    assert len(out) <= MAX_TEXT_CHARS


def test_idempotent() -> None:
    text = "intro " + "q" * 300 + " mid‍dle " + "https://e.co/" + "b" * 150
    once = clean_text(text)
    assert clean_text(once) == once
