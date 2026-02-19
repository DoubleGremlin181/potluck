"""Hashing utilities for deduplication and file identification.

This module provides hash functions for two levels of deduplication:

1. File-level (ImportRun.file_hash):
   - compute_file_hash() generates SHA256 of an import file/archive
   - Stored in ImportRun.file_hash (indexed for fast lookup)
   - Prevents re-processing the exact same export file

2. Entity-level (BaseEntity.content_hash):
   - compute_content_hash() generates SHA256 of entity content
   - Stored in BaseEntity.content_hash (indexed for fast lookup)
   - Prevents duplicate entities even across different imports
"""

import hashlib
from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, col, select

from potluck.core.exceptions import PipelineError
from potluck.core.logging import get_logger
from potluck.models.sources import ImportRun, ImportStatus

logger = get_logger(__name__)


# Buffer size for reading files in chunks (1 MB)
HASH_BUFFER_SIZE = 1024 * 1024


class DuplicateInfo(BaseModel):
    """Information about a duplicate file detection."""

    is_duplicate: bool
    """Whether the file is a duplicate."""

    file_hash: str
    """SHA256 hash of the file."""

    existing_import_run_id: UUID | None = None
    """ID of the ImportRun that previously processed this file."""

    existing_import_date: datetime | None = None
    """When the file was previously imported."""

    message: str | None = None
    """Human-readable message about the duplicate."""


def compute_file_hash(path: Path) -> str:
    """Compute SHA256 hash of a file.

    Reads the file in chunks for memory efficiency with large files.

    Args:
        path: Path to the file to hash.

    Returns:
        Hex-encoded SHA256 hash string.

    Raises:
        PipelineError: If the file does not exist or is not a file.
    """
    if not path.exists():
        raise PipelineError(f"File not found: {path}")

    if not path.is_file():
        raise PipelineError(f"Path is not a file: {path}")

    hasher = hashlib.sha256()

    with open(path, "rb") as f:
        while chunk := f.read(HASH_BUFFER_SIZE):
            hasher.update(chunk)

    return hasher.hexdigest()


def compute_content_hash(content: str | bytes) -> str:
    """Compute SHA256 hash of content.

    Used for deduplicating entity content.

    Args:
        content: String or bytes content to hash.

    Returns:
        Hex-encoded SHA256 hash string.
    """
    if isinstance(content, str):
        content = content.encode("utf-8")

    return hashlib.sha256(content).hexdigest()


async def check_file_duplicate(
    path: Path,
    session: "AsyncSession",
) -> DuplicateInfo:
    """Check if a file has been previously imported.

    Computes the file hash and checks against existing ImportRun records.

    Args:
        path: Path to the file to check.
        session: Database session for querying ImportRun.

    Returns:
        DuplicateInfo with details about whether this is a duplicate.
    """
    file_hash = compute_file_hash(path)

    stmt = (
        select(ImportRun)
        .where(ImportRun.file_hash == file_hash)
        .where(ImportRun.status == ImportStatus.COMPLETED)
        .order_by(col(ImportRun.started_at).desc())
        .limit(1)
    )

    result = await session.execute(stmt)
    existing_run = result.scalar_one_or_none()

    if existing_run:
        return DuplicateInfo(
            is_duplicate=True,
            file_hash=file_hash,
            existing_import_run_id=existing_run.id,
            existing_import_date=existing_run.started_at,
            message=(
                f"This file was previously imported on "
                f"{existing_run.started_at.strftime('%Y-%m-%d %H:%M')}. "
                f"Re-importing will create duplicate entities unless they have "
                f"matching content hashes."
            ),
        )

    return DuplicateInfo(
        is_duplicate=False,
        file_hash=file_hash,
        message=None,
    )


def check_file_duplicate_sync(
    path: Path,
    session: "Session",
) -> DuplicateInfo:
    """Synchronous version of check_file_duplicate.

    Args:
        path: Path to the file to check.
        session: Database session for querying ImportRun.

    Returns:
        DuplicateInfo with details about whether this is a duplicate.
    """
    file_hash = compute_file_hash(path)

    stmt = (
        select(ImportRun)
        .where(ImportRun.file_hash == file_hash)
        .where(ImportRun.status == ImportStatus.COMPLETED)
        .order_by(col(ImportRun.started_at).desc())
        .limit(1)
    )

    existing_run = session.exec(stmt).first()

    if existing_run:
        return DuplicateInfo(
            is_duplicate=True,
            file_hash=file_hash,
            existing_import_run_id=existing_run.id,
            existing_import_date=existing_run.started_at,
            message=(
                f"This file was previously imported on "
                f"{existing_run.started_at.strftime('%Y-%m-%d %H:%M')}. "
                f"Re-importing will create duplicate entities unless they have "
                f"matching content hashes."
            ),
        )

    return DuplicateInfo(
        is_duplicate=False,
        file_hash=file_hash,
        message=None,
    )
