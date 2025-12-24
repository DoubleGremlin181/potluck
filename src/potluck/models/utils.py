"""Utility functions and annotated types for Potluck models.

This module provides:
- `utc_now()`: Default factory for UTC timestamps
- `UTCDatetime`: Annotated type that auto-converts datetimes to UTC
- `IANATimezone`: Annotated type that validates IANA timezone strings
"""

from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import BeforeValidator


def utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime.

    Use as default_factory for timestamp fields.
    """
    return datetime.now(UTC)


def ensure_utc(v: datetime | None, source_timezone: str | None = None) -> datetime | None:
    """Convert datetime to UTC.

    Args:
        v: The datetime to convert
        source_timezone: IANA timezone string for interpreting naive datetimes.
            If provided and v is naive, v is treated as being in this timezone.
            If not provided and v is naive, v is assumed to be UTC.

    Returns:
        UTC-aware datetime, or None if input was None

    Examples:
        >>> ensure_utc(datetime(2024, 1, 1, 12, 0))  # naive, assumes UTC
        datetime(2024, 1, 1, 12, 0, tzinfo=UTC)

        >>> ensure_utc(datetime(2024, 1, 1, 12, 0), "America/New_York")  # naive + tz
        datetime(2024, 1, 1, 17, 0, tzinfo=UTC)  # converted from EST
    """
    if v is None:
        return None
    if v.tzinfo is None:
        # Naive datetime - use source_timezone if provided, else assume UTC
        if source_timezone:
            try:
                tz = ZoneInfo(source_timezone)
                return v.replace(tzinfo=tz).astimezone(UTC)
            except KeyError:
                # Invalid timezone, fall back to assuming UTC
                return v.replace(tzinfo=UTC)
        return v.replace(tzinfo=UTC)
    return v.astimezone(UTC)


def _ensure_utc(v: datetime | None) -> datetime | None:
    """Validator wrapper for ensure_utc (assumes UTC for naive datetimes)."""
    return ensure_utc(v)


def _validate_timezone(tz: str | None) -> str | None:
    """Validate and normalize an IANA timezone string.

    Args:
        tz: Timezone string to validate (e.g., 'America/New_York', 'UTC')

    Returns:
        The validated timezone string, or None if input was None

    Raises:
        ValueError: If the timezone string is invalid
    """
    if tz is None:
        return None
    try:
        ZoneInfo(tz)
        return tz
    except KeyError as err:
        raise ValueError(f"Invalid timezone: {tz}") from err


# Annotated types for use in model field definitions
UTCDatetime = Annotated[datetime | None, BeforeValidator(_ensure_utc)]
"""Datetime field that auto-converts to UTC. Naive datetimes assumed UTC."""

IANATimezone = Annotated[str | None, BeforeValidator(_validate_timezone)]
"""String field that validates as a valid IANA timezone (e.g., 'America/New_York')."""
