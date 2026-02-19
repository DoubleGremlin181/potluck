"""Common exception classes for Potluck."""


class PotluckError(Exception):
    """Base exception for all Potluck errors."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(self.message)


class ConfigurationError(PotluckError):
    """Raised when there is a configuration problem."""


class DatabaseError(PotluckError):
    """Raised when a database operation fails."""


class PipelineError(PotluckError):
    """Base exception for pipeline operations.

    Both ingestion and processing errors inherit from this class,
    allowing unified error handling for pipeline operations.
    """


class IngestionError(PipelineError):
    """Raised when data ingestion fails.

    Covers all ingestion-related errors including:
    - Source file/directory not found
    - Unsupported or corrupt archive formats
    - Archive extraction failures
    - File parsing errors (JSON, CSV, MBOX, etc.)
    """


class ProcessingError(PipelineError):
    """Raised when media processing fails.

    Covers all processing-related errors including:
    - Image/video file corruption or unsupported formats
    - OCR extraction failures
    - Face detection failures
    - EXIF parsing errors
    - Model loading errors
    """


class WebError(PotluckError):
    """Raised when a web UI operation fails."""


class SearchError(PotluckError):
    """Base exception for search operations.

    Covers all search-related errors including:
    - Query parsing errors
    - Retrieval failures
    - Ranking errors
    """


class InvalidSearchQueryError(SearchError):
    """Raised when a search query is malformed or invalid.

    Examples:
    - Empty query string
    - Invalid entity types specified
    - Invalid search mode
    """


class NoSearchableEntitiesError(SearchError):
    """Raised when no searchable entities match the query criteria.

    For example, when searching for entity types that don't have
    search support enabled.
    """
