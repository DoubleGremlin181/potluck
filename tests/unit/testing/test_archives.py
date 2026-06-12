"""Tests for potluck.testing.archives: synthetic archive builders."""

from pathlib import Path

import pytest

from potluck.ingest.readers import open_archive
from potluck.testing.archives import split_parts, write_archive

_MEMBERS: dict[str, bytes] = {
    "Takeout/Keep/a.json": b'{"text": "note a"}',
    "Takeout/Keep/b.json": b'{"text": "note b"}',
    "Takeout/Other/x.txt": b"hello",
}


# ---------------------------------------------------------------------------
# split_parts
# ---------------------------------------------------------------------------


def test_split_parts_round_robin() -> None:
    """5 members / 3 parts → round-robin sizes [2, 2, 1]; union preserved; all non-empty."""
    members = {f"file{i}.txt": f"content{i}".encode() for i in range(5)}
    parts = split_parts(members, 3)

    assert len(parts) == 3
    sizes = sorted((len(p) for p in parts), reverse=True)
    assert sizes == [2, 2, 1]
    # All parts non-empty
    assert all(len(p) > 0 for p in parts)
    # Union is exactly the original
    union: dict[str, bytes] = {}
    for p in parts:
        union.update(p)
    assert union == members


def test_split_parts_even_split() -> None:
    """6 members / 3 parts → equal sizes [2, 2, 2]."""
    members = {f"f{i}": b"x" for i in range(6)}
    parts = split_parts(members, 3)
    assert all(len(p) == 2 for p in parts)


def test_split_parts_single_part() -> None:
    """N members / 1 part → single dict with all members."""
    members = {f"f{i}": b"x" for i in range(4)}
    parts = split_parts(members, 1)
    assert len(parts) == 1
    assert parts[0] == members


def test_split_parts_more_parts_than_members_raises() -> None:
    """parts > len(members) raises ValueError — would yield empty dicts."""
    members = {"a": b"1", "b": b"2"}
    with pytest.raises(ValueError, match="parts"):
        split_parts(members, 3)


# ---------------------------------------------------------------------------
# write_archive determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["zip", "tgz"])
def test_write_archive_deterministic(tmp_path: Path, fmt: str) -> None:
    """Same members → byte-identical output on repeated calls (fixed mtimes)."""
    path1 = tmp_path / f"arch1.{fmt}"
    path2 = tmp_path / f"arch2.{fmt}"
    write_archive(path1, _MEMBERS, fmt)  # type: ignore[arg-type]
    write_archive(path2, _MEMBERS, fmt)  # type: ignore[arg-type]
    assert path1.read_bytes() == path2.read_bytes(), f"{fmt} output is not deterministic"


def test_write_archive_zip_members_deflated(tmp_path: Path) -> None:
    """Zip members must actually be compressed: real Takeout zips are deflated,
    so stored members would make fixtures and bench archives skip the
    decompression cost entirely."""
    import zipfile

    compressible = {"Takeout/Keep/big.json": b'{"text": "' + b"word " * 2000 + b'"}'}
    dest = write_archive(tmp_path / "arch.zip", compressible, "zip")

    with zipfile.ZipFile(dest) as zf:
        info = zf.infolist()[0]
        assert info.compress_type == zipfile.ZIP_DEFLATED
        assert info.compress_size < info.file_size


# ---------------------------------------------------------------------------
# write_archive round-trip (dir)
# ---------------------------------------------------------------------------


def test_write_archive_dir_round_trip(tmp_path: Path) -> None:
    """write_archive 'dir' creates real files readable from the filesystem."""
    dest = tmp_path / "arch_dir"
    write_archive(dest, _MEMBERS, "dir")
    for name, expected in _MEMBERS.items():
        assert (dest / name).read_bytes() == expected


# ---------------------------------------------------------------------------
# write_archive skips directory entries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["zip", "tgz", "dir"])
def test_write_archive_no_extra_dir_entries(tmp_path: Path, fmt: str) -> None:
    """write_archive does not add standalone directory entries."""
    dest = tmp_path / "arch_dir" if fmt == "dir" else tmp_path / f"arch.{fmt}"
    write_archive(dest, _MEMBERS, fmt)  # type: ignore[arg-type]

    # Re-open and confirm no name ends with '/'
    names = list(open_archive(dest).iter_names())
    assert not any(n.endswith("/") for n in names)
    assert set(names) == set(_MEMBERS.keys())
