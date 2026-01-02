"""Shared Celery utilities for task error handling and retry configuration.

This module provides common utilities used by both ingestion and processing
Celery tasks to ensure consistent error handling and retry behavior.
"""

from sqlalchemy.exc import InterfaceError, OperationalError

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF = 60  # seconds
RETRY_BACKOFF_MAX = 600  # 10 minutes


def is_transient_error(error: Exception) -> bool:
    """Check if exception is transient and should be retried.

    Transient errors include:
    - Database connection issues (OperationalError, InterfaceError)
    - Disk I/O errors (EIO, ENOSPC, EROFS)

    Args:
        error: The exception to check.

    Returns:
        True if the error is transient and the operation should be retried.
    """
    if isinstance(error, OperationalError | InterfaceError):
        return True
    # Disk I/O errors (EIO, ENOSPC, EROFS)
    return isinstance(error, OSError) and error.errno in (5, 28, 30)


def is_fatal_error(error: Exception) -> bool:
    """Check if exception is fatal and should not be retried.

    Fatal errors include:
    - FileNotFoundError (file is missing, won't appear on retry)
    - PermissionError (access denied, won't change on retry)

    Args:
        error: The exception to check.

    Returns:
        True if the error is fatal and the task should be rejected.
    """
    return isinstance(error, FileNotFoundError | PermissionError)
