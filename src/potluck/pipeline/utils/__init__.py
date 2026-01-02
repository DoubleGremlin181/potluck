"""Utility modules for pipeline operations.

This package provides common utilities used by both ingestion and processing stages:

- archive: Archive extraction (ZIP, TAR) with automatic cleanup
- hashing: SHA256 hashing for file and content deduplication
- parsers: File format parsers (CSV, JSON, MBOX)
"""

from potluck.pipeline.utils.archive import (
    ExtractedArchive,
    extract_archive,
    extracted,
    get_archive_type,
    is_archive,
)
from potluck.pipeline.utils.hashing import (
    HASH_BUFFER_SIZE,
    DuplicateInfo,
    check_file_duplicate,
    check_file_duplicate_sync,
    compute_content_hash,
    compute_file_hash,
)
from potluck.pipeline.utils.parsers import (
    DATE_FORMATS,
    MboxAttachment,
    MboxMessage,
    parse_csv,
    parse_datetime,
    parse_json,
    parse_mbox,
)

__all__ = [
    # archive
    "ExtractedArchive",
    "is_archive",
    "get_archive_type",
    "extract_archive",
    "extracted",
    # hashing
    "HASH_BUFFER_SIZE",
    "DuplicateInfo",
    "compute_file_hash",
    "compute_content_hash",
    "check_file_duplicate",
    "check_file_duplicate_sync",
    # parsers
    "DATE_FORMATS",
    "parse_datetime",
    "parse_json",
    "parse_csv",
    "MboxMessage",
    "MboxAttachment",
    "parse_mbox",
]
