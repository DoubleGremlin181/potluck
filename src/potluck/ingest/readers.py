"""Streaming archive readers for Google Takeout exports.

Supports zip, tgz (tar.gz), extracted directories, and multi-part sets.

Design contract: iteration is streaming / sequential.
- tar.gz is single-cursor sequential access: each yielded stream is valid only
  until the next iteration step. Consumers must read the stream before calling
  next() on the iterator.
- A fresh tarfile / ZipFile handle is opened per iteration call so each
  iter_names() / iter_members() call is independent and fd-safe.
"""

import fnmatch
import re
import tarfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from potluck.core.errors import UnsupportedArchiveError

# Matches multi-part Takeout filenames, e.g. takeout-20251212T171747Z-16-001.tgz
_MULTIPART_RE: re.Pattern[str] = re.compile(
    r"^(?P<stem>.+)-(?P<idx>\d{3})\.(?P<ext>zip|tgz|tar\.gz)$"
)


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
        ``fnmatch.fnmatch`` behaviour and is intentional for glob-style paths
        like ``'*Keep/*.json'``.
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
                if not info.is_dir() and fnmatch.fnmatch(info.filename, pattern):
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
                if m.isfile() and fnmatch.fnmatch(m.name, pattern):
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
            if fnmatch.fnmatch(name, pattern):
                file_path = self._path / name
                with file_path.open("rb") as f:
                    yield Member(name=name, size=file_path.stat().st_size), f


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
    """Detect and open a zip / .tgz / .tar.gz / directory archive.

    Multi-part sets (e.g. ``takeout-20251212-001.tgz``, ``…-002.tgz``, …) are
    detected by filename pattern and automatically combined into a
    :class:`MultiPartArchive`. Opening any part of the set loads the whole set.

    Raises :class:`~potluck.core.errors.UnsupportedArchiveError` for unrecognised
    extensions or paths that do not exist.
    """
    if path.is_dir():
        return DirArchive(path)

    if not path.exists():
        raise UnsupportedArchiveError(f"Path does not exist: {path}")

    # Multi-part detection
    m = _MULTIPART_RE.match(path.name)
    if m:
        stem = m.group("stem")
        ext = m.group("ext")
        parent = path.parent
        siblings: list[Path] = sorted(
            p
            for p in parent.glob(f"{stem}-???.{ext}")
            if (pm := _MULTIPART_RE.match(p.name)) is not None and pm.group("stem") == stem
        )
        if len(siblings) > 1:
            parts = tuple(_make_single_archive(p) for p in siblings)
            return MultiPartArchive(parts)
        # Only one file in the "set" → treat as plain archive
        return _make_single_archive(path)

    # Non-multi-part filename: detect by extension
    return _make_single_archive(path)
