"""Potluck exception hierarchy."""


class PotluckError(Exception):
    """Base class for all Potluck-specific errors."""


class MigrationError(PotluckError):
    """A schema migration failed and was rolled back."""


class FtsIntegrityError(PotluckError):
    """The FTS5 index is out of sync with the items content table."""
