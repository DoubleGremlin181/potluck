"""Tests for custom exceptions."""

import pytest

from potluck.core.exceptions import (
    ConfigurationError,
    DatabaseError,
    IngestionError,
    PotluckError,
)


class TestExceptions:
    """Tests for custom exceptions."""

    def test_potluck_error_base(self) -> None:
        """PotluckError stores message."""
        error = PotluckError("test error")
        assert error.message == "test error"
        assert str(error) == "test error"

    def test_configuration_error(self) -> None:
        """ConfigurationError inherits from PotluckError."""
        error = ConfigurationError("bad config")
        assert isinstance(error, PotluckError)
        assert error.message == "bad config"

    def test_database_error(self) -> None:
        """DatabaseError inherits from PotluckError."""
        error = DatabaseError("db failed")
        assert isinstance(error, PotluckError)

    def test_ingestion_error(self) -> None:
        """IngestionError inherits from PotluckError."""
        error = IngestionError("import failed")
        assert isinstance(error, PotluckError)

    def test_exceptions_catchable_as_potluck_error(self) -> None:
        """All custom exceptions can be caught as PotluckError."""
        exceptions = [
            ConfigurationError("msg"),
            DatabaseError("msg"),
            IngestionError("msg"),
        ]
        for exc in exceptions:
            with pytest.raises(PotluckError):
                raise exc
