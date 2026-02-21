"""Shared parsing utilities for web routers."""

from datetime import datetime

from potluck.core.logging import get_logger
from potluck.models.base import EntityType

logger = get_logger("web.utils")


def parse_optional_datetime(value: str | None, *, field_name: str = "date") -> datetime | None:
    """Parse an optional ISO datetime string, logging on invalid input.

    Returns None when *value* is falsy or unparseable.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        logger.warning("Ignoring invalid '%s' value: %s", field_name, value)
        return None


def parse_entity_types(
    raw: list[str],
    allowed: set[EntityType] | None = None,
) -> set[EntityType]:
    """Parse raw string values into a set of EntityType members.

    Invalid values are logged and skipped.  When *allowed* is provided,
    only entity types in that set are included in the result.
    """
    result: set[EntityType] = set()
    for t in raw:
        try:
            et = EntityType(t)
        except ValueError:
            logger.warning("Ignoring invalid entity type filter: %s", t)
            continue
        if allowed is not None and et not in allowed:
            continue
        result.add(et)
    return result
