"""Tests for potluck.ingest.readers: zip/tgz/dir archive readers + multi-part sets."""

import itertools
import random
import tracemalloc
from collections.abc import Generator
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


def test_open_archive_unknown_raises(tmp_path: Path) -> None:
    """UnsupportedArchiveError raised for .txt files and nonexistent paths."""
    txt_file = tmp_path / "data.txt"
    txt_file.write_bytes(b"hello")
    with pytest.raises(UnsupportedArchiveError):
        open_archive(txt_file)

    with pytest.raises(UnsupportedArchiveError):
        open_archive(tmp_path / "nonexistent.xyz")


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
