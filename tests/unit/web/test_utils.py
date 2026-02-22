"""Tests for web utility functions."""

import logging
from datetime import datetime

import pytest

from potluck.models.base import EntityType
from potluck.web.utils import escape_like, parse_entity_types, parse_optional_datetime


class TestEscapeLike:
    """Tests for escape_like SQL wildcard escaping."""

    def test_plain_string_unchanged(self) -> None:
        assert escape_like("hello world") == "hello world"

    def test_escapes_percent(self) -> None:
        assert escape_like("100%") == "100\\%"

    def test_escapes_underscore(self) -> None:
        assert escape_like("file_name") == "file\\_name"

    def test_escapes_backslash(self) -> None:
        assert escape_like("path\\to") == "path\\\\to"

    def test_escapes_all_wildcards(self) -> None:
        assert escape_like("100% of_things\\here") == "100\\% of\\_things\\\\here"

    def test_empty_string(self) -> None:
        assert escape_like("") == ""

    def test_multiple_wildcards(self) -> None:
        assert escape_like("%%__") == "\\%\\%\\_\\_"


class TestParseOptionalDatetime:
    """Tests for parse_optional_datetime."""

    def test_none_returns_none(self) -> None:
        assert parse_optional_datetime(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_optional_datetime("") is None

    def test_valid_iso_date(self) -> None:
        result = parse_optional_datetime("2024-01-15")
        assert result == datetime(2024, 1, 15)

    def test_valid_iso_datetime(self) -> None:
        result = parse_optional_datetime("2024-01-15T10:30:00")
        assert result == datetime(2024, 1, 15, 10, 30, 0)

    def test_invalid_date_returns_none(self) -> None:
        assert parse_optional_datetime("not-a-date") is None

    def test_invalid_date_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("potluck.web.utils")
        logger.addHandler(caplog.handler)
        try:
            with caplog.at_level(logging.WARNING):
                parse_optional_datetime("bad", field_name="since")
        finally:
            logger.removeHandler(caplog.handler)
        assert "Ignoring invalid 'since' value: bad" in caplog.text


class TestParseEntityTypes:
    """Tests for parse_entity_types."""

    def test_empty_list(self) -> None:
        assert parse_entity_types([]) == set()

    def test_valid_types(self) -> None:
        result = parse_entity_types(["media", "email"])
        assert result == {EntityType.MEDIA, EntityType.EMAIL}

    def test_invalid_types_skipped(self) -> None:
        result = parse_entity_types(["media", "invalid_type"])
        assert result == {EntityType.MEDIA}

    def test_invalid_types_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("potluck.web.utils")
        logger.addHandler(caplog.handler)
        try:
            with caplog.at_level(logging.WARNING):
                parse_entity_types(["bad_type"])
        finally:
            logger.removeHandler(caplog.handler)
        assert "Ignoring invalid entity type filter: bad_type" in caplog.text

    def test_allowed_filter(self) -> None:
        result = parse_entity_types(
            ["media", "email"],
            allowed={EntityType.MEDIA},
        )
        assert result == {EntityType.MEDIA}
