"""Tests for the Takeout Chrome history source plugin.

Testing private helpers (_parse_history) is intentional: the composite
identity policy, the µs timestamp discipline, and the incremental-JSON
containment are the public contract of this module and must be covered at
the unit level, from synthetic bytes.

Field names here mirror the real 2025-12 Takeout export (shape only — all
record content is synthetic).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from potluck.ingest.plugins import ParseContext, detect_sources, discover
from potluck.ingest.readers import open_archive
from potluck.ingest.sources.chrome import _parse_history, parse
from potluck.models.drafts import ActivityDraft
from potluck.models.items import ItemKind
from potluck.testing.archives import write_archive

_MEMBER = "Takeout/Chrome/History.json"

# 2023-05-11T08:30:00.123457Z — µs digits deliberately non-round.
_USEC = 1_683_793_800_123_457
_URL = "https://www.example.com/wiki/Synthetic_Page"


def _record(**overrides: object) -> dict[str, Any]:
    """One record in the real export's field order; None removes a field."""
    base: dict[str, Any] = {
        "favicon_url": "https://www.example.com/favicon.ico",
        "page_transition_qualifier": "CLIENT_REDIRECT",
        "title": "Synthetic Page",
        "url": _URL,
        "time_usec": _USEC,
        "client_id": "c3ludGgtY2xpZW50",
    }
    for key, value in overrides.items():
        if value is None:
            base.pop(key, None)
        else:
            base[key] = value
    return base


def _history(*records: object, **extra_sections: object) -> bytes:
    doc: dict[str, Any] = {"Browser History": list(records)}
    doc.update(extra_sections)
    return json.dumps(doc, ensure_ascii=False).encode()


def _drafts(data: bytes, member: str = _MEMBER) -> list[ActivityDraft]:
    return list(_parse_history(data, member))


def _eid(time_usec: int, url: str) -> str:
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    return f"chrome:{time_usec}:{digest}"


# ---------------------------------------------------------------------------
# Field mapping + timestamp fidelity
# ---------------------------------------------------------------------------


def test_basic_record_mapping() -> None:
    [d] = _drafts(_history(_record()))
    assert d.kind is ItemKind.ACTIVITY
    assert d.external_id == _eid(_USEC, _URL)
    assert d.ts == datetime(2023, 5, 11, 8, 30, 0, 123457, tzinfo=UTC)
    assert d.title == "Synthetic Page"
    assert d.text == f"Synthetic Page\n{_URL}"
    assert d.meta == {
        "page_transition_qualifier": "CLIENT_REDIRECT",
        "client_id": "c3ludGgtY2xpZW50",
    }


def test_timestamp_resolves_microseconds_exactly() -> None:
    """time_usec → UTC datetime via exact integer µs arithmetic — float
    seconds would round the last digit at 2020s magnitudes."""
    [d] = _drafts(_history(_record(time_usec=1_700_000_000_000_001)))
    assert d.ts == datetime(2023, 11, 14, 22, 13, 20, 1, tzinfo=UTC)


def test_time_usec_zero_means_unknown_timestamp() -> None:
    """Chrome writes 0 when it has no visit time — not a 1970 instant. The
    identity still embeds the exported 0 verbatim."""
    [d] = _drafts(_history(_record(time_usec=0)))
    assert d.ts is None
    assert d.external_id == _eid(0, _URL)


def test_empty_title_falls_back_to_host_and_path() -> None:
    [d] = _drafts(_history(_record(title="", url="https://www.example.com/some/page")))
    assert d.title == "www.example.com/some/page"
    assert d.text == "https://www.example.com/some/page"  # url only — never the derived title


def test_missing_title_falls_back_like_empty() -> None:
    [d] = _drafts(_history(_record(title=None)))
    assert d.title == "www.example.com/wiki/Synthetic_Page"


def test_fallback_title_survives_unparseable_url() -> None:
    """urlsplit rejects malformed IPv6 brackets; the raw url is the honest
    stand-in — the record must not be lost to a display nicety."""
    [d] = _drafts(_history(_record(title="", url="https://[bad/x")))
    assert d.title == "https://[bad/x"


def test_unicode_title_and_url_survive() -> None:
    [d] = _drafts(
        _history(_record(title="Zürich café 🎉", url="https://www.example.com/wiki/Zürich"))
    )
    assert d.title == "Zürich café 🎉"
    assert d.text == "Zürich café 🎉\nhttps://www.example.com/wiki/Zürich"


def test_legacy_page_transition_field_lands_in_meta() -> None:
    """Older exports (BrowserHistory.json generation) carry page_transition;
    both spellings pass through under their exported names."""
    [d] = _drafts(_history(_record(page_transition_qualifier=None, page_transition="LINK")))
    assert d.meta["page_transition"] == "LINK"
    assert "page_transition_qualifier" not in d.meta


def test_both_transition_fields_kept_verbatim() -> None:
    [d] = _drafts(_history(_record(page_transition="TYPED")))
    assert d.meta["page_transition"] == "TYPED"
    assert d.meta["page_transition_qualifier"] == "CLIENT_REDIRECT"


def test_favicon_url_is_dropped() -> None:
    """Browser chrome, not a personal record — never stored."""
    [d] = _drafts(_history(_record()))
    assert "favicon_url" not in d.meta


def test_absent_optional_fields_stay_out_of_meta() -> None:
    [d] = _drafts(
        _history(_record(page_transition_qualifier=None, client_id=None, favicon_url=None))
    )
    assert d.meta == {}


# ---------------------------------------------------------------------------
# Identity: chrome:<time_usec>:<url-hash prefix> + first-seen #N
# ---------------------------------------------------------------------------


def test_identity_is_time_and_url_composite() -> None:
    [d] = _drafts(_history(_record()))
    assert d.external_id == f"chrome:{_USEC}:" + hashlib.sha256(_URL.encode()).hexdigest()[:16]


def test_identical_records_get_first_seen_suffixes() -> None:
    """Byte-identical records in one export are numbered, never lost."""
    drafts = _drafts(_history(_record(), _record(), _record()))
    eids = [d.external_id or "" for d in drafts]
    assert "#" not in eids[0]
    assert eids[1] == eids[0] + "#2"
    assert eids[2] == eids[0] + "#3"


def test_same_time_different_urls_never_collide() -> None:
    """Two devices can log the same µs — the url hash keeps them distinct."""
    drafts = _drafts(
        _history(_record(url="https://www.example.com/a"), _record(url="https://www.example.com/b"))
    )
    assert len({d.external_id for d in drafts}) == 2


def test_same_url_different_times_never_collide() -> None:
    drafts = _drafts(_history(_record(time_usec=_USEC), _record(time_usec=_USEC + 1)))
    assert len({d.external_id for d in drafts}) == 2


def test_two_history_members_dedup_across_members(tmp_path: Path) -> None:
    """Two history members in one archive are re-exports of the same history:
    per-member counters give both copies identical external_ids, so the
    engine dedups them instead of double-importing."""
    body = _history(_record(), _record())
    members = {
        "Takeout/Chrome/History.json": body,
        "Takeout2/Chrome/History.json": body,
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    drafts = list(parse(open_archive(archive), ParseContext()))
    assert len(drafts) == 4
    assert len({d.external_id for d in drafts}) == 2


# ---------------------------------------------------------------------------
# Containment: bad records, bad JSON, foreign shapes
# ---------------------------------------------------------------------------


def test_record_without_url_is_skipped_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(_history(_record(url=None), _record()))
    assert len(drafts) == 1
    assert any("url" in r.message for r in caplog.records)


def test_record_without_time_usec_is_skipped_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Identity needs the visit time — a record without one cannot be minted."""
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(_history(_record(time_usec=None), _record()))
    assert len(drafts) == 1
    assert any("time_usec" in r.message for r in caplog.records)


@pytest.mark.parametrize("bad", ["1683793800123457", 1.5, True])
def test_non_integer_time_usec_is_skipped_with_warning(
    bad: object, caplog: pytest.LogCaptureFixture
) -> None:
    """The verified real shape is an integer; a foreign type must never be
    guessed into an identity (bool is an int subclass — still foreign)."""
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(_history(_record(time_usec=bad)))
    assert drafts == []
    assert any("time_usec" in r.message for r in caplog.records)


def test_out_of_range_time_usec_keeps_record_undated(caplog: pytest.LogCaptureFixture) -> None:
    """The id is the identity — content must survive an absurd timestamp."""
    with caplog.at_level(logging.WARNING):
        [d] = _drafts(_history(_record(time_usec=10**19)))
    assert d.ts is None
    assert d.external_id == _eid(10**19, _URL)
    assert any("time_usec" in r.message for r in caplog.records)


def test_non_object_record_is_skipped_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(_history("stray string", _record()))
    assert len(drafts) == 1
    assert any("not an object" in r.message for r in caplog.records)


def test_malformed_json_warns_and_keeps_yielded_records(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Truncated download mid-array: rows already yielded stand, one WARNING
    names the member, no exception escapes."""
    good = json.dumps(_record())
    data = ('{"Browser History": [' + good + ", {broken").encode()
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(data)
    assert len(drafts) == 1
    assert drafts[0].title == "Synthetic Page"
    assert any("JSON error" in r.message for r in caplog.records)


def test_member_without_history_key_warns(caplog: pytest.LogCaptureFixture) -> None:
    """A renamed/foreign shape must never import as zero items silently."""
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(json.dumps({"Typed Url": []}).encode())
    assert drafts == []
    assert any("Browser History" in r.message for r in caplog.records)


def test_non_array_history_value_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        drafts = _drafts(json.dumps({"Browser History": {"nested": True}}).encode())
    assert drafts == []
    assert any("Browser History" in r.message for r in caplog.records)


def test_empty_history_array_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    """A fresh profile legitimately has no visits — not a parse failure."""
    with caplog.at_level(logging.WARNING):
        assert _drafts(_history()) == []
    assert not caplog.records


def test_history_key_position_does_not_matter() -> None:
    """Key order is a serialization accident; sections before the array
    (Typed Url, Session) are decoded and discarded."""
    doc = {
        "Typed Url": [],
        "Session": [{"session_tag": "synthetic", "tab": {"index": 1}}],
        "Browser History": [_record()],
        "Shared Tab Group": [],
    }
    [d] = _drafts(json.dumps(doc).encode())
    assert d.title == "Synthetic Page"


def test_bom_is_tolerated() -> None:
    [d] = _drafts(b"\xef\xbb\xbf" + _history(_record()))
    assert d.title == "Synthetic Page"


# ---------------------------------------------------------------------------
# Detection + parse() over archives
# ---------------------------------------------------------------------------


def test_detection_matches_export_layout_precisely(tmp_path: Path) -> None:
    matches = {
        # The real 2025-12 member and its older-generation name, at the
        # standard Takeout/ nesting, re-zipped deeper, or root-relative.
        "Takeout/Chrome/History.json": True,
        "Takeout/Chrome/BrowserHistory.json": True,
        "Chrome/History.json": True,
        "Chrome/BrowserHistory.json": True,
        "wrapper/Takeout/Chrome/BrowserHistory.json": True,
        # Every OTHER member of the real Chrome folder must never match.
        "Takeout/Chrome/Extensions.json": False,
        "Takeout/Chrome/Settings.json": False,
        "Takeout/Chrome/OS Settings.json": False,
        "Takeout/Chrome/Device Information.json": False,
        "Takeout/Chrome/Addresses and more.json": False,
        "Takeout/Chrome/Dictionary.csv": False,
        "Takeout/Chrome/Bookmarks.html": False,
        "Takeout/Chrome/Reading List.html": False,
        "Takeout/My Activity/Chrome/MyActivity.html": False,
        # Generic names NEVER detect — the generic JSON ingester's (#150)
        # territory; the Chrome/ parent segment is the anchor.
        "History.json": False,
        "BrowserHistory.json": False,
        "NotChrome/History.json": False,
        "history/History.json": False,
        "Takeout/Chrome/History.json.bak": False,
        "takeout/chrome/history.json": False,  # matching is case-sensitive
    }
    plugin = discover()["chrome"]
    for name, expected in matches.items():
        assert plugin.detect.matches(name) is expected, name

    members = {"Takeout/Chrome/History.json": _history(_record())}
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    assert [p.name for p in detect_sources(open_archive(archive))] == ["chrome"]


def test_parse_reads_history_and_skips_sibling_members(tmp_path: Path) -> None:
    """Extensions/Settings are Chrome state, not personal records — never
    read, even though they match the *.json member pass."""
    members = {
        "Takeout/Chrome/History.json": _history(_record()),
        "Takeout/Chrome/Extensions.json": b'{"Extensions": [{"name": "Decoy"}]}',
        "Takeout/Chrome/Settings.json": b'{"Settings": []}',
        "Takeout/Chrome/Dictionary.csv": b"decoyword\n",
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    drafts = list(parse(open_archive(archive), ParseContext()))
    assert len(drafts) == 1
    assert isinstance(drafts[0], ActivityDraft)
    assert drafts[0].title == "Synthetic Page"


def test_combined_takeout_detects_all_products(tmp_path: Path) -> None:
    """One Takeout with Keep + Chrome surfaces both plugins (#195 path)."""
    members = {
        "Takeout/Chrome/History.json": _history(_record()),
        "Takeout/Keep/note.json": b'{"title": "x", "textContent": "y"}',
    }
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    names = [p.name for p in detect_sources(open_archive(archive))]
    assert names == ["chrome", "google_keep"]


def test_parse_handles_nested_layout(tmp_path: Path) -> None:
    members = {"wrapper/Takeout/Chrome/History.json": _history(_record())}
    archive = write_archive(tmp_path / "export.zip", members, "zip")
    assert [p.name for p in detect_sources(open_archive(archive))] == ["chrome"]
    assert len(list(parse(open_archive(archive), ParseContext()))) == 1


def test_parse_empty_archive_yields_nothing(tmp_path: Path) -> None:
    archive = write_archive(tmp_path / "empty.zip", {"decoy/readme.md": b"x"}, "zip")
    assert list(parse(open_archive(archive), ParseContext())) == []
