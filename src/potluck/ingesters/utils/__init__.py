"""Utility modules for data ingestion."""

from potluck.core.exceptions import (
    ArchiveError,
    ExtractionError,
    ParseError,
    UnsupportedArchiveError,
)
from potluck.ingesters.utils.archive import (
    ExtractedArchive,
    extract_archive,
    extracted,
    get_archive_type,
    is_archive,
)
from potluck.ingesters.utils.dedup import (
    DuplicateInfo,
    check_file_duplicate_sync,
    compute_content_hash,
    compute_file_hash,
)
from potluck.ingesters.utils.parsers import (
    MboxAttachment,
    MboxMessage,
    parse_csv,
    parse_datetime,
    parse_json,
    parse_mbox,
)
from potluck.ingesters.utils.progress import (
    IngestionStats,
    LoggingProgressCallback,
    NoOpProgressCallback,
    ProgressCallback,
    ProgressTracker,
)

__all__ = [
    # Archive
    "ArchiveError",
    "ExtractedArchive",
    "ExtractionError",
    "UnsupportedArchiveError",
    "extract_archive",
    "extracted",
    "get_archive_type",
    "is_archive",
    # Dedup
    "DuplicateInfo",
    "check_file_duplicate_sync",
    "compute_content_hash",
    "compute_file_hash",
    # Parsers
    "MboxAttachment",
    "MboxMessage",
    "ParseError",
    "parse_csv",
    "parse_datetime",
    "parse_json",
    "parse_mbox",
    # Progress
    "IngestionStats",
    "LoggingProgressCallback",
    "NoOpProgressCallback",
    "ProgressCallback",
    "ProgressTracker",
]
