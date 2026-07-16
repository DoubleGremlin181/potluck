"""Potluck exception hierarchy."""


class PotluckError(Exception):
    """Base class for all Potluck-specific errors."""


class MigrationError(PotluckError):
    """A schema migration failed and was rolled back."""


class FtsIntegrityError(PotluckError):
    """The FTS5 index is out of sync with the items content table."""


class UnsupportedArchiveError(PotluckError):
    """The path is not a recognised archive format (zip / tgz / tar.gz / directory)."""


class UnknownSourceError(PotluckError):
    """No registered source plugin recognises the given archive."""


class DuplicateSourceError(PotluckError):
    """A source plugin with this name is already registered in the registry."""


class ItemNotFoundError(PotluckError):
    """No item with the given id exists in the database."""


class ImportNotFoundError(PotluckError):
    """No import run with the given id exists in the ledger."""


class ImportInProgressError(PotluckError):
    """A background import is already running; only one runs at a time (#132)."""


class ImportRunningError(PotluckError):
    """The rm/forget target belongs to an import run that is still running (#153)."""


class SourceNotFoundError(PotluckError):
    """No source with the given name exists in the database (#153)."""


class UploadTooLargeError(PotluckError):
    """An uploaded archive exceeds the configured max_upload_bytes limit."""


class InvalidCursorError(PotluckError):
    """The search pagination cursor is malformed or of an unsupported version."""


class GDriveAuthError(PotluckError):
    """Google Drive authorization is missing, revoked, or failed (#152).

    Recovering requires the user to (re-)run ``potluck gdrive auth`` — the
    puller surfaces this in status and never retries a dead credential in a
    loop (it waits for the token file to change).
    """


class GDriveApiError(PotluckError):
    """A Google Drive API call failed for a non-auth reason (#152): rate
    limit, server error, or a download-integrity (md5) mismatch. Transient —
    the puller backs off and retries on later cycles."""
