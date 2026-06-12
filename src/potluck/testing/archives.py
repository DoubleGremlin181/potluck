"""Synthetic archive builders for tests and fixtures.

Shipped inside the package so tests and fixture generators share one source.
All output is deterministic (fixed mtimes, sorted member order).
stdlib only — no external dependencies.
"""

import gzip
import tarfile
import zipfile
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from typing import Literal


def write_archive(
    dest: Path,
    members: Mapping[str, bytes],
    fmt: Literal["zip", "tgz", "dir"],
) -> Path:
    """Materialise members ({posix_name: content}) as dest zip/tgz/directory.

    Member names are sorted before writing for deterministic output ordering.
    tgz members use mtime=0 for byte-identical reproducibility.
    zip members use date_time=(1980, 1, 1, 0, 0, 0) (the zip epoch minimum).

    Returns dest.
    """
    sorted_names = sorted(members.keys())

    if fmt == "dir":
        for name in sorted_names:
            file_path = dest / name
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(members[name])

    elif fmt == "zip":
        with zipfile.ZipFile(dest, "w") as zf:
            for name in sorted_names:
                zip_info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                # writestr with a ZipInfo uses the ZipInfo's own compress_type
                # (default ZIP_STORED), ignoring the ZipFile-level setting —
                # pass it explicitly or nothing is ever actually compressed.
                zf.writestr(zip_info, members[name], compress_type=zipfile.ZIP_DEFLATED)

    elif fmt == "tgz":
        # Use gzip.GzipFile directly with filename="" and mtime=0 so the gzip
        # header is byte-identical regardless of the destination path or wall time.
        with (
            dest.open("wb") as raw,
            gzip.GzipFile(filename="", mode="wb", mtime=0, fileobj=raw) as gz,
            tarfile.open(fileobj=gz, mode="w:") as tf,
        ):
            for name in sorted_names:
                content = members[name]
                tar_info = tarfile.TarInfo(name=name)
                tar_info.size = len(content)
                tar_info.mtime = 0
                tf.addfile(tar_info, BytesIO(content))

    return dest


def split_parts(members: Mapping[str, bytes], parts: int) -> list[dict[str, bytes]]:
    """Round-robin split of members into N non-empty dicts (for multi-part fixture sets).

    Member names are sorted before splitting for deterministic assignment.
    Example: 5 members / 3 parts → part sizes [2, 2, 1].

    Raises ValueError if *parts* > ``len(members)`` because that would yield
    empty dicts, violating the "N non-empty dicts" contract.
    """
    if parts > len(members):
        raise ValueError(
            f"parts ({parts}) exceeds number of members ({len(members)}): "
            "every part must be non-empty"
        )
    sorted_names = sorted(members.keys())
    result: list[dict[str, bytes]] = [{} for _ in range(parts)]
    for i, name in enumerate(sorted_names):
        result[i % parts][name] = members[name]
    return result
