"""Streaming archive readers for Google Takeout exports.

Supports zip, tgz (tar.gz), extracted directories, multi-part sets, and bare
single-file exports (plugins only speak Archive, so a lone export file is
exposed as a one-member archive).

Design contract: iteration is streaming / sequential.
- tar.gz is single-cursor sequential access: each yielded stream is valid only
  until the next iteration step. Consumers must read the stream before calling
  next() on the iterator.
- A fresh tarfile / ZipFile handle is opened per iteration call so each
  iter_names() / iter_members() call is independent and fd-safe.
"""

import fnmatch
import glob
import re
import tarfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from potluck.core.errors import UnsupportedArchiveError

# Multi-part Takeout naming. Two generations exist (both seen on real exports):
#   old: takeout-20240115T123456Z-001.zip        <stem>-<part>
#   new: takeout-20251212T171747Z-14-001.tgz     <stem>-<file>-<part>
# _MULTIPART_RE strips the zero-padded part index; its stem match is
# intentionally loose (any stem) — that is the historical behaviour and what
# the synthetic generator emits. _TAKEOUT_FILE_RE then strips the new-style
# file number, but ONLY when the remaining stem still ends in a Takeout export
# timestamp — that anchor is the sole safety argument: the timestamp uniquely
# identifies one export, so anything sharing (prefix, timestamp) IS the same
# export and the extra strip cannot over-group. Without the anchor a second
# numeric strip would merge unrelated sets (report-2023-001 vs
# report-2024-001). The file number is deliberately uncapped (\d+): a
# >999-file export (multi-TB Photos at 1-2 GB chunks) must not silently split
# its high-numbered parts out of the set.
_MULTIPART_RE: re.Pattern[str] = re.compile(
    r"^(?P<stem>.+)-(?P<part>\d{3})\.(?P<ext>zip|tgz|tar\.gz)$"
)
_TAKEOUT_FILE_RE: re.Pattern[str] = re.compile(r"^(?P<stem>.+-\d{8}T\d{6}Z)-(?P<file>\d+)$")


def _parse_part_name(name: str) -> tuple[str, str, tuple[int, int]] | None:
    """Split an archive filename into (set stem, ext, numeric order) — or None.

    Order is (file, part) so real sets sort numerically (9 < 12 < 16, and
    2-001 < 2-002 < 10-001); old-style names have no file number and use 0.
    """
    m = _MULTIPART_RE.match(name)
    if m is None:
        return None
    stem, part, ext = m.group("stem"), int(m.group("part")), m.group("ext")
    fm = _TAKEOUT_FILE_RE.match(stem)
    if fm is not None:
        return fm.group("stem"), ext, (int(fm.group("file")), part)
    return stem, ext, (0, part)


@dataclass(frozen=True)
class Member:
    """Metadata for a single file member inside a (possibly multi-part) archive."""

    name: str  # posix path inside the LOGICAL archive, e.g. 'Takeout/Keep/note.json'
    size: int  # uncompressed byte length


class Archive(Protocol):
    """Streaming, sequential-access view of a (possibly multi-part) archive."""

    def iter_names(self) -> Iterator[str]:
        """All member names (files only), lazily, in archive order."""
        ...

    def iter_members(self, pattern: str) -> Iterator[tuple[Member, IO[bytes]]]:
        """Members whose name fnmatch-es *pattern*, with a readable binary stream.

        CONTRACT: each stream is valid only until the next iteration step
        (tar is a single sequential cursor). Consumers must read the entire
        stream content before advancing the iterator.

        Note: ``*`` in *pattern* crosses ``/`` separators — this is standard
        ``fnmatch`` behaviour and is intentional for glob-style paths like
        ``'*Keep/*.json'``. Matching is case-sensitive on every platform
        (``fnmatch.fnmatchcase``): member names are virtual posix paths, so
        the host OS's case folding must not apply.
        """
        ...


# ---------------------------------------------------------------------------
# ZipArchive
# ---------------------------------------------------------------------------


class ZipArchive:
    """Read-only streaming view of a single zip file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def iter_names(self) -> Iterator[str]:
        """Names from the central directory — no decompression, files only."""
        with zipfile.ZipFile(self._path) as zf:
            for info in zf.infolist():
                if not info.is_dir():
                    yield info.filename

    def iter_members(self, pattern: str) -> Iterator[tuple[Member, IO[bytes]]]:
        """Yields (Member, stream) pairs for members matching *pattern*.

        The ZipFile is opened for the duration of this iteration and closed
        when the generator is exhausted or garbage-collected. Each inner
        stream is closed when the iterator advances to the next member.
        """
        with zipfile.ZipFile(self._path) as zf:
            for info in zf.infolist():
                if not info.is_dir() and fnmatch.fnmatchcase(info.filename, pattern):
                    with zf.open(info) as stream:
                        yield Member(name=info.filename, size=info.file_size), stream


# ---------------------------------------------------------------------------
# TarArchive
# ---------------------------------------------------------------------------


class TarArchive:
    """Read-only streaming view of a single tar.gz (or any tarfile format) file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def iter_names(self) -> Iterator[str]:
        """File names, lazily in archive order. Never calls getmembers()."""
        with tarfile.open(self._path, "r:*") as tf:
            for m in tf:
                if m.isfile():
                    yield m.name

    def iter_members(self, pattern: str) -> Iterator[tuple[Member, IO[bytes]]]:
        """Yields (Member, stream) for members matching *pattern*.

        Unmatched members are skipped without loading their data into memory
        (the tarfile cursor reads their headers and streams over the data
        in small internal chunks — never buffering the full content).

        A fresh tarfile handle is opened per call because a tar cursor is
        single-use. Each yielded stream is valid only until the next call
        to ``next()`` on the iterator.
        """
        with tarfile.open(self._path, "r:*") as tf:
            for m in tf:
                if m.isfile() and fnmatch.fnmatchcase(m.name, pattern):
                    fileobj = tf.extractfile(m)
                    if fileobj is None:  # impossible: m.isfile() guarantees a regular file
                        raise AssertionError("extractfile returned None for a regular file")
                    yield Member(name=m.name, size=m.size), fileobj


# ---------------------------------------------------------------------------
# DirArchive
# ---------------------------------------------------------------------------


class DirArchive:
    """Read-only view of an extracted directory tree."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def iter_names(self) -> Iterator[str]:
        """posix-style relative paths, sorted for determinism."""
        for file_path in sorted(self._path.rglob("*")):
            # Path.is_file() follows symlinks, so symlinked files are included
            # (unlike TarInfo.isfile(), which only matches regular files).
            if file_path.is_file():
                yield file_path.relative_to(self._path).as_posix()

    def iter_members(self, pattern: str) -> Iterator[tuple[Member, IO[bytes]]]:
        """Yields (Member, stream) for files matching *pattern*."""
        for name in self.iter_names():
            if fnmatch.fnmatchcase(name, pattern):
                file_path = self._path / name
                with file_path.open("rb") as f:
                    yield Member(name=name, size=file_path.stat().st_size), f


# ---------------------------------------------------------------------------
# SingleFileArchive
# ---------------------------------------------------------------------------


class SingleFileArchive:
    """A bare (non-archive) export file as a one-member archive.

    Some products export a single plain file — the Android on-device
    Timeline.json, a lone WhatsApp chat .txt — and plugins only speak
    Archive, so the reader seam adapts. The sole member's name is the file's
    basename; detection globs therefore need a root-relative alternative to
    match it.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def iter_names(self) -> Iterator[str]:
        """The single member name: the file's basename."""
        yield self._path.name

    def iter_members(self, pattern: str) -> Iterator[tuple[Member, IO[bytes]]]:
        """The (Member, stream) pair when the basename matches *pattern*.

        A fresh handle per call — each iteration is independent, the same
        contract the other implementations honour.
        """
        if fnmatch.fnmatchcase(self._path.name, pattern):
            with self._path.open("rb") as f:
                yield Member(name=self._path.name, size=self._path.stat().st_size), f


# ---------------------------------------------------------------------------
# MultiPartArchive
# ---------------------------------------------------------------------------


class MultiPartArchive:
    """Chains multiple archive parts into one logical archive, in order."""

    def __init__(self, parts: tuple[Archive, ...]) -> None:
        self._parts = parts

    def iter_names(self) -> Iterator[str]:
        """Names from all parts in order."""
        for part in self._parts:
            yield from part.iter_names()

    def iter_members(self, pattern: str) -> Iterator[tuple[Member, IO[bytes]]]:
        """Matching members from all parts in order."""
        for part in self._parts:
            yield from part.iter_members(pattern)


# ---------------------------------------------------------------------------
# open_archive
# ---------------------------------------------------------------------------


def _make_single_archive(path: Path) -> "ZipArchive | TarArchive":
    """Create a single-file archive by extension. Raises UnsupportedArchiveError."""
    name = path.name
    if name.endswith(".zip"):
        return ZipArchive(path)
    if name.endswith(".tgz") or name.endswith(".tar.gz"):
        return TarArchive(path)
    raise UnsupportedArchiveError(
        f"Unsupported archive format '{path.suffix}': {path}. Expected .zip, .tgz, or .tar.gz"
    )


def open_archive(path: Path) -> Archive:
    """Detect and open a zip / .tgz / .tar.gz / directory / plain-file archive.

    Multi-part sets — old naming (``takeout-20240115T123456Z-001.tgz``,
    ``…-002.tgz``, …) and real current Takeout naming
    (``takeout-20251212T171747Z-9-001.tgz``, ``…-12-001.tgz``, …) — are
    detected by filename pattern and automatically combined into a
    :class:`MultiPartArchive`, ordered numerically by (file, part). Opening
    any part of the set loads the whole set. An existing file without an
    archive extension opens as a :class:`SingleFileArchive` — bare
    single-file exports are real import shapes (#148's Timeline.json).

    Raises :class:`~potluck.core.errors.UnsupportedArchiveError` for paths
    that do not exist.
    """
    if path.is_dir():
        return DirArchive(path)

    if not path.exists():
        raise UnsupportedArchiveError(f"Path does not exist: {path}")

    # Multi-part detection
    parsed = _parse_part_name(path.name)
    if parsed is not None:
        stem, ext, _ = parsed
        parent = path.parent
        # glob.escape: the stem is user-controlled and may contain glob
        # metacharacters ('[', ']', '*') — match it literally or siblings are
        # silently missed. The '*' spans both '-NNN' and '-N-NNN' tails;
        # _parse_part_name re-validates every candidate, and the stem equality
        # check rejects files whose stem merely extends this one (e.g.
        # 'takeout-test-9-001' globbed from stem 'takeout-test').
        # Zero-padded and bare file numbers ('-014-' vs '-14-') collide to
        # equal order keys; the Path in the tuple breaks the tie deterministically.
        siblings: list[tuple[tuple[int, int], Path]] = sorted(
            (candidate[2], p)
            for p in parent.glob(f"{glob.escape(stem)}-*.{ext}")
            if (candidate := _parse_part_name(p.name)) is not None and candidate[0] == stem
        )
        if len(siblings) > 1:
            parts = tuple(_make_single_archive(p) for _, p in siblings)
            return MultiPartArchive(parts)
        # Only one file in the "set" → treat as plain archive
        return _make_single_archive(path)

    # Non-multi-part filename: archives by extension; any other existing
    # plain file is a single-member archive (its basename the sole member).
    # The not-exists check above keeps typo'd paths failing fast.
    if path.name.endswith((".zip", ".tgz", ".tar.gz")):
        return _make_single_archive(path)
    return SingleFileArchive(path)
