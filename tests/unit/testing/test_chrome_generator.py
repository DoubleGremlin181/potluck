"""Deterministic Chrome Takeout generator (potluck.testing.chrome)."""

import json
from pathlib import Path

from potluck.testing.chrome import (
    chrome_members,
    expected_duplicate_suffix_count,
    expected_empty_title_count,
    expected_visit_count,
    visit_time_usec,
    write_chrome_takeout,
)

_HISTORY = "Takeout/Chrome/History.json"


def _records(members: dict[str, bytes]) -> list[dict[str, object]]:
    doc = json.loads(members[_HISTORY])
    assert isinstance(doc, dict)
    history = doc["Browser History"]
    assert isinstance(history, list)
    return history


def test_same_arguments_produce_identical_bytes() -> None:
    assert chrome_members(24, seed=5) == chrome_members(24, seed=5)


def test_different_seeds_produce_different_histories() -> None:
    assert chrome_members(24, seed=5)[_HISTORY] != chrome_members(24, seed=6)[_HISTORY]


def test_member_set_mirrors_real_export_shape() -> None:
    """The real Chrome folder: History.json beside sibling members the parser
    must never read (detection-precision decoys)."""
    members = chrome_members(4)
    assert sorted(members) == [
        "Takeout/Chrome/Dictionary.csv",
        "Takeout/Chrome/Extensions.json",
        _HISTORY,
        "Takeout/Chrome/Settings.json",
    ]
    doc = json.loads(members[_HISTORY])
    # The real 2025-12 export's top-level sections, in its order.
    assert list(doc) == ["Browser History", "Typed Url", "Session", "Shared Tab Group"]
    # Record field names and order mirror the real export exactly.
    assert list(_records(members)[0]) == [
        "favicon_url",
        "page_transition_qualifier",
        "title",
        "url",
        "time_usec",
        "client_id",
    ]


def test_records_and_duplicates_match_closed_forms() -> None:
    count = 60
    records = _records(chrome_members(count, seed=7))
    assert len(records) == expected_visit_count(count) == count

    dup_indices = [i for i in range(count) if i > 0 and i % 25 == 24]
    assert len(dup_indices) == expected_duplicate_suffix_count(count)
    for i in dup_indices:
        assert records[i] == records[i - 1]  # verbatim copies (identity input)

    empty = [i for i, r in enumerate(records) if r["title"] == ""]
    assert len(empty) == expected_empty_title_count(count)
    assert empty  # the title-fallback path is always exercised


def test_timestamps_are_microsecond_precise_and_out_of_order() -> None:
    """Non-round µs on every visit; the periodic back-step makes the array
    non-monotonic (real histories interleave devices), while non-duplicate
    records never share a time_usec."""
    count = 40
    records = _records(chrome_members(count, seed=7))
    usecs: list[int] = []
    for r in records:
        u = r["time_usec"]
        assert isinstance(u, int)
        usecs.append(u)
    assert usecs[1] == visit_time_usec(1)
    assert all(u % 1_000_000 != 0 for u in usecs[1:3])
    assert any(b < a for a, b in zip(usecs, usecs[1:], strict=False))  # out of order
    non_dup = [u for i, u in enumerate(usecs) if not (i > 0 and i % 25 == 24)]
    assert len(set(non_dup)) == len(non_dup)


def test_unicode_titles_and_urls_appear() -> None:
    records = _records(chrome_members(40, seed=7))
    assert any("🎉" in str(r["title"]) for r in records)
    assert any("Zürich" in str(r["url"]) for r in records)


def test_closed_forms_are_pure_ints() -> None:
    assert expected_visit_count(0) == 0
    assert expected_duplicate_suffix_count(0) == 0
    assert expected_empty_title_count(0) == 0
    assert expected_duplicate_suffix_count(60) == 2  # i in {24, 49}
    assert isinstance(visit_time_usec(3), int)


def test_write_export_dir_layout(tmp_path: Path) -> None:
    root = write_chrome_takeout(tmp_path, 6, seed=3, fmt="dir")
    assert root.name == "chrome-synth-001"
    assert (root / "Takeout" / "Chrome" / "History.json").is_file()
    assert (root / "Takeout" / "Chrome" / "Extensions.json").is_file()


def test_write_export_zip_naming(tmp_path: Path) -> None:
    archive = write_chrome_takeout(tmp_path, 6, seed=3)
    assert archive.name == "chrome-synth-001.zip"
