"""Snowflake ids, timestamps and the time arguments of R25-R27.

Ids are strings end to end (R25). Datetimes are converted to synthetic snowflakes with the
Discord epoch; naive datetimes, datetimes before the epoch and datetimes in the future are
validation errors.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Final

from discord_mcp.errors import DiscordMcpError

DISCORD_EPOCH_MS: Final = 1420070400000
SNOWFLAKE_PATTERN: Final = r"^[0-9]{15,21}$"
_SNOWFLAKE_RE: Final = re.compile(SNOWFLAKE_PATTERN)
MAX_SNOWFLAKE: Final = 2**64 - 1
_EPOCH: Final = datetime.fromtimestamp(DISCORD_EPOCH_MS / 1000, UTC)


def is_snowflake(value: object) -> bool:
    """True for a string of 15-21 digits whose value fits in 64 bits (R25)."""
    return (
        isinstance(value, str)
        and _SNOWFLAKE_RE.fullmatch(value) is not None
        and int(value) <= MAX_SNOWFLAKE
    )


def validate_snowflake(value: str, field: str) -> str:
    """Return ``value`` if it is a snowflake, else raise a validation error naming ``field``."""
    if not is_snowflake(value):
        raise DiscordMcpError(
            "validation",
            f"{field} must be a Discord snowflake id (15-21 digits)",
        )
    return value


def snowflake_created_at(snowflake: str) -> datetime:
    """The creation time encoded in a snowflake (R27), UTC."""
    ms = (int(snowflake) >> 22) + DISCORD_EPOCH_MS
    return datetime.fromtimestamp(ms / 1000, UTC)


def to_iso(value: datetime) -> str:
    """ISO 8601 UTC with millisecond precision and a ``Z`` suffix (R27)."""
    if value.tzinfo is None:
        raise ValueError("naive datetime")
    text = value.astimezone(UTC).isoformat(timespec="milliseconds")
    return text.replace("+00:00", "Z")


def discord_timestamp(value: object) -> str | None:
    """Normalise a Discord ISO timestamp string to ``Z`` form; None when absent or unparseable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return to_iso(parsed)


def datetime_to_snowflake(value: datetime, now: datetime, field: str) -> str:
    """Synthetic snowflake ``(ms - epoch) << 22`` for an aware datetime (R26)."""
    if value.tzinfo is None:
        raise DiscordMcpError(
            "validation",
            f"{field} must be a snowflake or an ISO 8601 datetime with an offset or Z",
        )
    if value < _EPOCH:
        raise DiscordMcpError("validation", f"{field} is before the Discord epoch (2015-01-01)")
    if value > now:
        raise DiscordMcpError("validation", f"{field} is in the future")
    ms = (value - _EPOCH) // timedelta(milliseconds=1)
    return str(ms << 22)


def parse_time_argument(value: str, field: str) -> datetime | str:
    """Classify a time argument: a validated snowflake string, or an aware datetime (R26).

    A snowflake is returned as validated text. An ISO 8601 string must carry an explicit offset
    or ``Z``; naive values are rejected.
    """
    if is_snowflake(value):
        return value
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise DiscordMcpError(
            "validation",
            f"{field} must be a snowflake or an ISO 8601 datetime with an offset or Z",
        ) from None
    if parsed.tzinfo is None:
        raise DiscordMcpError(
            "validation",
            f"{field} must be a snowflake or an ISO 8601 datetime with an offset or Z",
        )
    return parsed


def time_argument_to_snowflake(value: str, field: str, now: datetime) -> str:
    """A time argument as a snowflake for ``before`` / ``after`` / ``around`` query parameters."""
    parsed = parse_time_argument(value, field)
    if isinstance(parsed, str):
        return parsed
    return datetime_to_snowflake(parsed, now, field)


def time_argument_to_datetime(value: str, field: str, now: datetime) -> datetime:
    """A time argument as an aware datetime (snowflakes use their encoded creation time)."""
    parsed = parse_time_argument(value, field)
    if isinstance(parsed, str):
        return snowflake_created_at(parsed)
    if parsed > now:
        raise DiscordMcpError("validation", f"{field} is in the future")
    if parsed < _EPOCH:
        raise DiscordMcpError("validation", f"{field} is before the Discord epoch (2015-01-01)")
    return parsed
