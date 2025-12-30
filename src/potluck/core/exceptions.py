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


class EntityNotFoundError(PotluckError):
    """Raised when an entity is not found."""

    def __init__(self, entity_type: str, entity_id: str | int) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        super().__init__(f"{entity_type} with id '{entity_id}' not found")


class IngestionError(PotluckError):
    """Raised when data ingestion fails."""


class ProcessingError(PotluckError):
    """Raised when media/content processing fails."""


class ArchiveError(IngestionError):
    """Base exception for archive-related errors."""


class UnsupportedArchiveError(ArchiveError):
    """Raised when the archive format is not supported."""


class ExtractionError(ArchiveError):
    """Raised when archive extraction fails."""


class ParseError(IngestionError):
    """Raised when parsing fails."""


class SourceNotFoundError(IngestionError):
    """Raised when a source file or directory is not found."""


class InvalidPathError(IngestionError):
    """Raised when a path is invalid (e.g., directory when file expected)."""
