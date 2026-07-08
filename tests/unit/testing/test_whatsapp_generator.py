"""The synthetic WhatsApp generator: deterministic, PII-safe, parser-aligned."""

import subprocess
import sys
from pathlib import Path

from potluck.ingest.plugins import ParseContext, detect_sources
from potluck.ingest.readers import open_archive
from potluck.ingest.sources.whatsapp import _parse_chat, parse
from potluck.models.drafts import MessageDraft
from potluck.testing.whatsapp import (
    Locale,
    expected_media_reference_count,
    expected_message_count,
    synthetic_chat_lines,
    write_whatsapp_export,
)

_GUARD = Path(__file__).resolve().parents[3] / "scripts" / "check_fixtures.py"


def _parse_lines(count: int, seed: int = 42, locale: Locale = "us") -> list[MessageDraft]:
    text = "\n".join(synthetic_chat_lines(count, seed, locale=locale)) + "\n"
    member = "_chat.txt" if locale == "ios" else "WhatsApp Chat with Chat.txt"
    counters: dict[str, int] = {}
    return list(_parse_chat(text.encode(), member, counters))


def test_generator_is_deterministic() -> None:
    a = list(synthetic_chat_lines(60, seed=7, locale="eu"))
    b = list(synthetic_chat_lines(60, seed=7, locale="eu"))
    c = list(synthetic_chat_lines(60, seed=8, locale="eu"))
    assert a == b
    assert a != c


def test_locale_line_shapes() -> None:
    us = next(iter(synthetic_chat_lines(1, seed=42, locale="us")))
    eu = next(iter(synthetic_chat_lines(1, seed=42, locale="eu")))
    ios = next(iter(synthetic_chat_lines(1, seed=42, locale="ios")))
    assert us.startswith("3/17/23, 9:00 AM - ")
    assert eu.startswith("17/03/2023, 09:00 - ")
    assert ios.startswith("[3/17/23, 9:00:00 AM] ")


def test_parser_yields_expected_counts_per_locale() -> None:
    for locale in ("us", "eu", "ios"):
        drafts = _parse_lines(120, locale=locale)
        assert len(drafts) == expected_message_count(120), locale
        media_refs = sum(len(d.media) for d in drafts)
        assert media_refs == expected_media_reference_count(120), locale


def test_us_and_eu_locales_resolve_identical_timestamps() -> None:
    """The same logical messages rendered month-first 12h and day-first 24h
    must parse to the same instants — the locale-inference proof."""
    us = _parse_lines(80, locale="us")
    eu = _parse_lines(80, locale="eu")
    assert [d.ts for d in us] == [d.ts for d in eu]


def test_duplicate_rule_produces_occurrence_suffix() -> None:
    drafts = _parse_lines(60)
    suffixed = [d.external_id for d in drafts if d.external_id and "#" in d.external_id]
    assert len(suffixed) == 1  # i=48 duplicates i=47 verbatim
    assert suffixed[0].endswith("#2")


def test_multiline_emoji_and_rtl_content_present() -> None:
    drafts = _parse_lines(60)
    texts = [d.text for d in drafts if d.text]
    assert any("\n" in t for t in texts)
    assert any("🎉" in t or "🚀" in t or "🥘" in t or "✨" in t for t in texts)
    assert any("مرحبا" in t or "שלום" in t for t in texts)


def test_generated_export_is_pii_clean(tmp_path: Path) -> None:
    """Generator output must satisfy the committed-fixture policy (the golden
    fixture is exactly this output)."""
    out = write_whatsapp_export(tmp_path, 200, seed=7, locales=("us", "eu", "ios"), fmt="dir")
    proc = subprocess.run(
        [sys.executable, str(_GUARD), str(out)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stdout


def test_export_zip_detects_and_parses_end_to_end(tmp_path: Path) -> None:
    archive_path = write_whatsapp_export(
        tmp_path, 50, seed=42, locales=("us", "ios"), chats_per_locale=2, fmt="zip"
    )
    assert archive_path.name == "whatsapp-synth-001.zip"
    archive = open_archive(archive_path)
    assert [p.name for p in detect_sources(archive)] == ["whatsapp"]
    drafts = list(parse(archive, ParseContext()))
    assert len(drafts) == 4 * expected_message_count(50)
    assert len({d.chat_key for d in drafts if isinstance(d, MessageDraft)}) == 4
