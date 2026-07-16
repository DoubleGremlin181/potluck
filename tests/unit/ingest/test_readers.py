"""Tests for potluck.ingest.readers: zip/tgz/dir archive readers + multi-part sets."""

import itertools
import os
import random
import tracemalloc
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import pytest

from potluck.core.errors import UnsupportedArchiveError
from potluck.ingest.readers import open_archive
from potluck.testing.archives import split_parts, write_archive

# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

NESTED_MEMBERS: dict[str, bytes] = {
    "Takeout/Keep/a.json": b'{"text": "note a"}',
    "Takeout/Keep/img.jpg": b"\xff\xd8\xff",
    "Takeout/Other/x.txt": b"hello",
}

ALL_MEMBERS: dict[str, bytes] = {
    "Takeout/Keep/a.json": b"a",
    "Takeout/Keep/b.json": b"b",
    "Takeout/Keep/c.json": b"c",
    "Takeout/Keep/d.json": b"d",
    "Takeout/Other/x.txt": b"x",
}


def _dest(tmp_path: Path, fmt: Literal["zip", "tgz", "dir"]) -> Path:
    """Canonical destination path for the given format."""
    if fmt == "dir":
        return tmp_path / "archive_dir"
    return tmp_path / f"archive.{fmt}"


# ---------------------------------------------------------------------------
# test_iter_names_lists_files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["zip", "tgz", "dir"])
def test_iter_names_lists_files(tmp_path: Path, fmt: Literal["zip", "tgz", "dir"]) -> None:
    """iter_names yields exactly the file members — no directory entries."""
    dest = _dest(tmp_path, fmt)
    write_archive(dest, NESTED_MEMBERS, fmt)
    archive = open_archive(dest)
    names = list(archive.iter_names())
    assert set(names) == set(NESTED_MEMBERS.keys())
    # No directory-only entries (entries must not end with '/')
    for name in names:
        assert not name.endswith("/")


# ---------------------------------------------------------------------------
# test_iter_members_pattern_filters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["zip", "tgz", "dir"])
def test_iter_members_pattern_filters(tmp_path: Path, fmt: Literal["zip", "tgz", "dir"]) -> None:
    """iter_members('*Keep/*.json') yields only json members with correct contents."""
    dest = _dest(tmp_path, fmt)
    write_archive(dest, NESTED_MEMBERS, fmt)
    archive = open_archive(dest)
    results = [(m.name, stream.read()) for m, stream in archive.iter_members("*Keep/*.json")]
    assert len(results) == 1
    name, content = results[0]
    assert name == "Takeout/Keep/a.json"
    assert content == b'{"text": "note a"}'


# ---------------------------------------------------------------------------
# test_streams_readable_sequentially  (tgz only — tar is sequential cursor)
# ---------------------------------------------------------------------------


def test_streams_readable_sequentially(tmp_path: Path) -> None:
    """Multiple tgz matches can each be fully read before advancing the iterator."""
    members: dict[str, bytes] = {
        "Takeout/Keep/a.json": b'{"text": "note a"}',
        "Takeout/Keep/b.json": b'{"text": "note b"}',
        "Takeout/Other/x.txt": b"hello",
    }
    dest = tmp_path / "archive.tgz"
    write_archive(dest, members, "tgz")
    archive = open_archive(dest)
    results: dict[str, bytes] = {}
    for m, stream in archive.iter_members("*Keep/*.json"):
        results[m.name] = stream.read()
    assert results == {
        "Takeout/Keep/a.json": b'{"text": "note a"}',
        "Takeout/Keep/b.json": b'{"text": "note b"}',
    }


# ---------------------------------------------------------------------------
# test_multipart_union
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["zip", "tgz"])
def test_multipart_union(tmp_path: Path, fmt: Literal["zip", "tgz"]) -> None:
    """3-part set: opening via any part yields the complete union of all members."""
    parts_data = split_parts(ALL_MEMBERS, 3)
    paths: list[Path] = []
    for i, part_members in enumerate(parts_data, 1):
        dest = tmp_path / f"takeout-test-{i:03d}.{fmt}"
        write_archive(dest, part_members, fmt)
        paths.append(dest)

    # Open via part 1
    names1 = set(open_archive(paths[0]).iter_names())
    # Open via part 2 — must still load the whole set
    names2 = set(open_archive(paths[1]).iter_names())

    assert names1 == set(ALL_MEMBERS.keys())
    assert names2 == set(ALL_MEMBERS.keys())


@pytest.mark.parametrize("fmt", ["zip", "tgz"])
def test_multipart_stem_with_glob_metacharacters(
    tmp_path: Path, fmt: Literal["zip", "tgz"]
) -> None:
    """Sibling discovery must treat the stem literally: a renamed set like
    'takeout [2024]-001.zip' still loads every part (no silent data loss)."""
    parts_data = split_parts(ALL_MEMBERS, 3)
    paths: list[Path] = []
    for i, part_members in enumerate(parts_data, 1):
        dest = tmp_path / f"takeout [2024]-{i:03d}.{fmt}"
        write_archive(dest, part_members, fmt)
        paths.append(dest)

    names = set(open_archive(paths[0]).iter_names())
    assert names == set(ALL_MEMBERS.keys())


@pytest.mark.parametrize("fmt", ["zip", "tgz", "dir"])
def test_iter_members_is_case_sensitive(tmp_path: Path, fmt: Literal["zip", "tgz", "dir"]) -> None:
    """Member matching is case-sensitive on every platform: archive member
    names are virtual posix paths, so Windows must not match more than Linux."""
    dest = _dest(tmp_path, fmt)
    write_archive(dest, {"Takeout/KEEP/a.JSON": b"{}", "Takeout/Keep/b.json": b"{}"}, fmt)
    archive = open_archive(dest)
    matched = [m.name for m, _ in archive.iter_members("*Keep/*.json")]
    assert matched == ["Takeout/Keep/b.json"]


# ---------------------------------------------------------------------------
# test_open_archive_detects
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["zip", "tgz", "dir"])
def test_open_archive_detects(tmp_path: Path, fmt: Literal["zip", "tgz", "dir"]) -> None:
    """open_archive auto-detects all supported formats."""
    dest = _dest(tmp_path, fmt)
    write_archive(dest, {"Takeout/a.txt": b"hello"}, fmt)
    archive = open_archive(dest)
    names = list(archive.iter_names())
    assert "Takeout/a.txt" in names


# ---------------------------------------------------------------------------
# test_open_archive_unknown_raises
# ---------------------------------------------------------------------------


def test_open_archive_nonexistent_raises(tmp_path: Path) -> None:
    """UnsupportedArchiveError still fails fast on typo'd paths."""
    with pytest.raises(UnsupportedArchiveError):
        open_archive(tmp_path / "nonexistent.xyz")


# ---------------------------------------------------------------------------
# SingleFileArchive: bare (non-archive) export files
# ---------------------------------------------------------------------------


def test_open_archive_plain_file_is_single_member_archive(tmp_path: Path) -> None:
    """An existing plain file opens as a one-member archive whose member name
    is the file's basename — bare single-file exports (the Android
    Timeline.json, a lone WhatsApp chat .txt) are real import shapes."""
    plain = tmp_path / "Timeline.json"
    plain.write_bytes(b'{"semanticSegments": []}')
    archive = open_archive(plain)
    assert list(archive.iter_names()) == ["Timeline.json"]


def test_single_file_archive_iter_members_matches_pattern(tmp_path: Path) -> None:
    plain = tmp_path / "WhatsApp Chat with Bo Sample.txt"
    plain.write_bytes(b"3/17/23, 9:00 AM - Bo Sample: synthetic hello\n")
    archive = open_archive(plain)

    matched = [(m.name, stream.read()) for m, stream in archive.iter_members("*.txt")]
    assert matched == [
        ("WhatsApp Chat with Bo Sample.txt", b"3/17/23, 9:00 AM - Bo Sample: synthetic hello\n")
    ]
    assert list(archive.iter_members("*.json")) == []


def test_single_file_archive_member_size(tmp_path: Path) -> None:
    plain = tmp_path / "data.csv"
    plain.write_bytes(b"a,b\n1,2\n")
    archive = open_archive(plain)
    sizes = [member.size for member, _ in archive.iter_members("*.csv")]
    assert sizes == [8]


def test_single_file_archive_iterations_are_independent(tmp_path: Path) -> None:
    """Each iter_members call opens a fresh handle (the per-call contract the
    other Archive implementations honour) — two-pass parsers re-read."""
    plain = tmp_path / "Timeline.json"
    plain.write_bytes(b"{}")
    archive = open_archive(plain)
    for _ in range(2):
        contents = [stream.read() for _, stream in archive.iter_members("Timeline.json")]
        assert contents == [b"{}"]


def test_plain_file_beside_multipart_set_stays_separate(tmp_path: Path) -> None:
    """A bare file never joins an adjacent multi-part set, and opening a real
    part still loads the whole set (multi-part detection is unaffected)."""
    write_archive(tmp_path / "takeout-20240115T123456Z-001.zip", {"a.txt": b"a"}, "zip")
    write_archive(tmp_path / "takeout-20240115T123456Z-002.zip", {"b.txt": b"b"}, "zip")
    plain = tmp_path / "takeout-20240115T123456Z-notes.txt"
    plain.write_bytes(b"plain")

    assert list(open_archive(plain).iter_names()) == ["takeout-20240115T123456Z-notes.txt"]
    names = list(open_archive(tmp_path / "takeout-20240115T123456Z-001.zip").iter_names())
    assert names == ["a.txt", "b.txt"]


# ---------------------------------------------------------------------------
# Member.mtime (#150): every Archive implementation reports it, or None
# ---------------------------------------------------------------------------


def test_member_mtime_zip(tmp_path: Path) -> None:
    """Zip DOS timestamps carry no zone — read as UTC (the unknown-zone
    policy), deterministic across hosts. The generator pins the zip epoch."""
    dest = tmp_path / "archive.zip"
    write_archive(dest, {"Takeout/a.txt": b"hello"}, "zip")
    [(member, _)] = list(open_archive(dest).iter_members("*.txt"))
    assert member.mtime == datetime(1980, 1, 1, tzinfo=UTC).timestamp()


def test_member_mtime_tgz(tmp_path: Path) -> None:
    """Tar carries an mtime header; the generator writes 0 (the epoch)."""
    dest = tmp_path / "archive.tgz"
    write_archive(dest, {"Takeout/a.txt": b"hello"}, "tgz")
    [(member, _)] = list(open_archive(dest).iter_members("*.txt"))
    assert member.mtime == 0.0


def test_member_mtime_dir(tmp_path: Path) -> None:
    dest = tmp_path / "archive_dir"
    write_archive(dest, {"Takeout/a.txt": b"hello"}, "dir")
    stamp = datetime(2024, 3, 1, 8, 0, tzinfo=UTC).timestamp()
    os.utime(dest / "Takeout/a.txt", (stamp, stamp))
    [(member, _)] = list(open_archive(dest).iter_members("*.txt"))
    assert member.mtime == stamp


def test_member_mtime_single_file(tmp_path: Path) -> None:
    plain = tmp_path / "note.md"
    plain.write_bytes(b"# hi\n")
    stamp = datetime(2024, 3, 1, 8, 0, tzinfo=UTC).timestamp()
    os.utime(plain, (stamp, stamp))
    [(member, _)] = list(open_archive(plain).iter_members("*.md"))
    assert member.mtime == stamp


def test_member_mtime_multipart_passthrough(tmp_path: Path) -> None:
    parts_data = split_parts(ALL_MEMBERS, 2)
    paths: list[Path] = []
    for i, part_members in enumerate(parts_data, 1):
        dest = tmp_path / f"takeout-test-{i:03d}.zip"
        write_archive(dest, part_members, "zip")
        paths.append(dest)
    zip_epoch = datetime(1980, 1, 1, tzinfo=UTC).timestamp()
    mtimes = [m.mtime for m, _ in open_archive(paths[0]).iter_members("*")]
    assert mtimes == [zip_epoch] * len(ALL_MEMBERS)


def test_mtime_ts_interpretation() -> None:
    """The consumer-side helper: positive epoch -> aware UTC instant; 0,
    negative, and absent read as no timestamp (epoch-0 is tar's 'unset'
    convention — the Keep epoch-0-is-absent posture)."""
    from potluck.ingest.readers import Member, mtime_ts

    stamp = datetime(2024, 3, 1, 8, 0, tzinfo=UTC)
    assert mtime_ts(Member(name="a", size=1, mtime=stamp.timestamp())) == stamp
    assert mtime_ts(Member(name="a", size=1, mtime=0.0)) is None
    assert mtime_ts(Member(name="a", size=1, mtime=-7200.0)) is None
    assert mtime_ts(Member(name="a", size=1, mtime=None)) is None


# ---------------------------------------------------------------------------
# test_tgz_large_member_flat_memory
# ---------------------------------------------------------------------------


def test_tgz_large_member_flat_memory(tmp_path: Path) -> None:
    """Unmatched 32 MiB tgz member never materialises in Python memory.

    tracemalloc peak for iterating '*Keep/*.json' must stay well below
    the member size, proving the data is streamed-and-discarded, not buffered.
    """
    rng = random.Random(0)
    big_data = rng.randbytes(32 * 1024 * 1024)  # 32 MiB incompressible-ish
    members: dict[str, bytes] = {
        "Takeout/Big/blob.bin": big_data,
        "Takeout/Keep/note.json": b'{"text": "small"}',
    }
    dest = tmp_path / "big.tgz"
    write_archive(dest, members, "tgz")

    archive = open_archive(dest)
    tracemalloc.start()
    try:
        results = [(m.name, stream.read()) for m, stream in archive.iter_members("*Keep/*.json")]
    finally:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    assert len(results) == 1
    assert results[0][0] == "Takeout/Keep/note.json"
    # Peak traced allocation must be well below the 32 MiB unmatched member
    assert peak < 8 * 1024 * 1024, f"peak was {peak / 1024 / 1024:.1f} MiB — unmatched data leaked"


# ---------------------------------------------------------------------------
# test_tgz_iter_names_lazy_early_exit
# ---------------------------------------------------------------------------


def test_tgz_iter_names_lazy_early_exit(tmp_path: Path) -> None:
    """Taking the first name from a tgz via islice completes and allows generator close."""
    members = {f"Takeout/Keep/{i}.json": f"item {i}".encode() for i in range(10)}
    dest = tmp_path / "lazy.tgz"
    write_archive(dest, members, "tgz")
    archive = open_archive(dest)

    # Cast to Generator so we can call .close() — iter_names() is a generator function
    names_gen = cast(Generator[str], archive.iter_names())
    first = next(itertools.islice(names_gen, 1))
    assert first in members

    # Closing the generator before exhausting it must not raise
    names_gen.close()


# ---------------------------------------------------------------------------
# test_multipart_iter_members_roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["zip", "tgz"])
def test_multipart_iter_members_roundtrip(tmp_path: Path, fmt: Literal["zip", "tgz"]) -> None:
    """iter_members('*') over a multi-part set returns all members with correct contents."""
    parts_data = split_parts(ALL_MEMBERS, 3)
    paths: list[Path] = []
    for i, part_members in enumerate(parts_data, 1):
        dest = tmp_path / f"takeout-test-{i:03d}.{fmt}"
        write_archive(dest, part_members, fmt)
        paths.append(dest)

    result = {m.name: stream.read() for m, stream in open_archive(paths[0]).iter_members("*")}
    assert result == ALL_MEMBERS


# ---------------------------------------------------------------------------
# test_solo_multipart_named_file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["zip", "tgz"])
def test_solo_multipart_named_file(tmp_path: Path, fmt: Literal["zip", "tgz"]) -> None:
    """A single archive-001.{fmt} with no siblings opens as a plain archive."""
    dest = tmp_path / f"takeout-solo-001.{fmt}"
    write_archive(dest, NESTED_MEMBERS, fmt)
    archive = open_archive(dest)
    names = set(archive.iter_names())
    assert names == set(NESTED_MEMBERS.keys())


# ---------------------------------------------------------------------------
# Real Takeout part naming: takeout-<timestamp>-<file>-<part>.<ext>
# (defect found on a real 4-part 2025 export — the parts silently opened solo)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["zip", "tgz"])
def test_multipart_real_takeout_naming_groups(tmp_path: Path, fmt: Literal["zip", "tgz"]) -> None:
    """The real Takeout naming (takeout-<ts>-<N>-001) groups into ONE set,
    ordered numerically by N (9 < 12 < 14 < 16 — not lexicographic), and
    opening ANY part loads the whole set."""
    file_nos = [9, 12, 14, 16]
    for n in file_nos:
        write_archive(
            tmp_path / f"takeout-20251212T171747Z-{n}-001.{fmt}",
            {f"Takeout/Part{n}/f.txt": b"x"},
            fmt,
        )
    expected = [f"Takeout/Part{n}/f.txt" for n in file_nos]
    for n in file_nos:
        opened = open_archive(tmp_path / f"takeout-20251212T171747Z-{n}-001.{fmt}")
        assert list(opened.iter_names()) == expected


def test_multipart_real_naming_orders_file_then_part(tmp_path: Path) -> None:
    """Within one set: sub-parts of the same file come before the next file
    (2-001 < 2-002 < 10-001), all compared numerically."""
    parts = [(2, 1), (2, 2), (10, 1)]
    for file_no, part_no in parts:
        write_archive(
            tmp_path / f"takeout-20251212T171747Z-{file_no}-{part_no:03d}.tgz",
            {f"Takeout/F{file_no}P{part_no}/f.txt": b"x"},
            "tgz",
        )
    opened = open_archive(tmp_path / "takeout-20251212T171747Z-10-001.tgz")
    assert list(opened.iter_names()) == [f"Takeout/F{f}P{p}/f.txt" for f, p in parts]


def test_multipart_large_file_numbers_group(tmp_path: Path) -> None:
    """File numbers are not capped: part -1000-001 of a >999-file export
    (multi-TB Photos at 1-2 GB chunks) still joins the set — the timestamp
    anchor alone decides membership."""
    for n in (2, 1000):
        write_archive(
            tmp_path / f"takeout-20251212T171747Z-{n}-001.tgz",
            {f"Takeout/Part{n}/f.txt": b"x"},
            "tgz",
        )
    opened = open_archive(tmp_path / "takeout-20251212T171747Z-1000-001.tgz")
    assert list(opened.iter_names()) == ["Takeout/Part2/f.txt", "Takeout/Part1000/f.txt"]


def test_multipart_different_timestamps_never_group(tmp_path: Path) -> None:
    """Two exports with different timestamps are different sets, even when
    file/part numbers collide."""
    old = tmp_path / "takeout-20250101T000000Z-1-001.tgz"
    new = tmp_path / "takeout-20251212T171747Z-1-001.tgz"
    write_archive(old, {"Takeout/Old/a.txt": b"a"}, "tgz")
    write_archive(new, {"Takeout/New/b.txt": b"b"}, "tgz")
    assert list(open_archive(old).iter_names()) == ["Takeout/Old/a.txt"]
    assert list(open_archive(new).iter_names()) == ["Takeout/New/b.txt"]


def test_multipart_year_like_middle_never_groups(tmp_path: Path) -> None:
    """The over-grouping trap: a naive 'strip two trailing numeric groups'
    would merge report-2023-001 and report-2024-001 into one 'report' set.
    Without a Takeout timestamp anchor, the middle number stays in the stem."""
    a = tmp_path / "report-2023-001.tgz"
    b = tmp_path / "report-2024-001.tgz"
    write_archive(a, {"reports/2023.csv": b"a"}, "tgz")
    write_archive(b, {"reports/2024.csv": b"b"}, "tgz")
    assert list(open_archive(a).iter_names()) == ["reports/2023.csv"]
    assert list(open_archive(b).iter_names()) == ["reports/2024.csv"]


def test_multipart_solo_real_named_part_opens_plain(tmp_path: Path) -> None:
    """A lone real-named part (no siblings) opens as a plain archive."""
    dest = tmp_path / "takeout-20251212T171747Z-14-001.tgz"
    write_archive(dest, NESTED_MEMBERS, "tgz")
    assert set(open_archive(dest).iter_names()) == set(NESTED_MEMBERS.keys())


def test_no_part_suffix_file_stays_separate_from_adjacent_set(tmp_path: Path) -> None:
    """A file without a part suffix never joins an adjacent set sharing its
    prefix — and opening it never pulls the set in."""
    lone = tmp_path / "takeout-20251212T171747Z.tgz"
    write_archive(lone, {"Lone/l.txt": b"l"}, "tgz")
    for n in (9, 12):
        write_archive(
            tmp_path / f"takeout-20251212T171747Z-{n}-001.tgz",
            {f"Takeout/Part{n}/f.txt": b"x"},
            "tgz",
        )
    assert list(open_archive(lone).iter_names()) == ["Lone/l.txt"]
    opened = open_archive(tmp_path / "takeout-20251212T171747Z-9-001.tgz")
    assert list(opened.iter_names()) == ["Takeout/Part9/f.txt", "Takeout/Part12/f.txt"]


def test_multipart_sibling_scan_keeps_stems_exact(tmp_path: Path) -> None:
    """Sibling discovery must not swallow files whose stem merely EXTENDS the
    set's stem: 'takeout-test-9-001' is not part of the 'takeout-test' set,
    and vice versa."""
    set_a1 = tmp_path / "takeout-test-001.tgz"
    set_a2 = tmp_path / "takeout-test-002.tgz"
    set_b = tmp_path / "takeout-test-9-001.tgz"
    write_archive(set_a1, {"A/one.txt": b"1"}, "tgz")
    write_archive(set_a2, {"A/two.txt": b"2"}, "tgz")
    write_archive(set_b, {"B/nine.txt": b"9"}, "tgz")
    assert list(open_archive(set_a1).iter_names()) == ["A/one.txt", "A/two.txt"]
    assert list(open_archive(set_b).iter_names()) == ["B/nine.txt"]
